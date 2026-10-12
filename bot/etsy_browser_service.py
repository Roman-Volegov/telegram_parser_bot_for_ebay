from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
from pathlib import Path
from urllib.request import ProxyHandler, build_opener

logger = logging.getLogger("etsy-browser-service")


def _profile_dir() -> Path:
    return Path(
        os.getenv("ETSY_BROWSER_PROFILE_DIR", "/app/data/etsy-browser-profile")
    )


def _chromium_executable() -> Path:
    candidates = sorted(
        Path("/ms-playwright").glob("chromium-*/chrome-linux64/chrome"),
        reverse=True,
    )
    if not candidates:
        raise RuntimeError("Playwright Chromium executable not found")
    return candidates[0]


def _read_int(path: str) -> int | None:
    try:
        value = Path(path).read_text(encoding="utf-8").strip()
        return None if value == "max" else int(value)
    except (OSError, ValueError):
        return None


def pid_threshold_reached(
    current: int | None,
    maximum: int | None,
    *,
    threshold_percent: int,
) -> bool:
    if current is None or maximum is None or maximum <= 0:
        return False
    return current * 100 >= maximum * threshold_percent


def _oom_kill_count() -> int:
    try:
        values = {}
        for line in Path("/sys/fs/cgroup/memory.events").read_text().splitlines():
            key, value = line.split(maxsplit=1)
            values[key] = int(value)
        return values.get("oom_kill", 0)
    except (OSError, ValueError):
        return 0


async def _wait_cdp_ready(timeout_sec: float = 30) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_sec
    url = "http://127.0.0.1:9224/json/version"
    opener = build_opener(ProxyHandler({}))
    while asyncio.get_running_loop().time() < deadline:
        try:
            await asyncio.to_thread(lambda: opener.open(url, timeout=1).read())
            return
        except Exception:
            await asyncio.sleep(0.25)
    raise RuntimeError("Chromium CDP did not become ready")


async def _start_chromium() -> asyncio.subprocess.Process:
    profile_dir = _profile_dir()
    profile_dir.mkdir(parents=True, exist_ok=True)
    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket", "lockfile"):
        path = profile_dir / name
        try:
            if path.exists() or path.is_symlink():
                path.unlink()
        except OSError:
            logger.warning("Failed to remove stale Chromium lock %s", path)

    args = [
        str(_chromium_executable()),
        "--remote-debugging-address=0.0.0.0",
        "--remote-debugging-port=9224",
        "--remote-allow-origins=*",
        f"--user-data-dir={profile_dir}",
        "--disable-blink-features=AutomationControlled",
        "--disable-breakpad",
        "--disable-crash-reporter",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--disable-background-networking",
        "--disable-component-update",
        "--disable-default-apps",
        "--disable-extensions",
        "--disable-sync",
        "--no-default-browser-check",
        "--no-first-run",
        "--no-sandbox",
        "--renderer-process-limit=2",
        "--window-size=1366,900",
        "--lang=en-US",
    ]
    proxy = os.getenv("ETSY_BROWSER_PROXY", "").strip()
    if proxy:
        args.append(f"--proxy-server={proxy}")
    args.append("about:blank")

    process = await asyncio.create_subprocess_exec(
        *args,
        start_new_session=True,
    )
    try:
        await _wait_cdp_ready()
    except Exception:
        await _terminate_process_group(process)
        raise
    logger.info("Chromium ready via CDP (pid=%s)", process.pid)
    return process


async def _terminate_process_group(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(process.wait(), timeout=8)
    except TimeoutError:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await process.wait()


async def _write_response(
    writer: asyncio.StreamWriter,
    status: str,
    payload: dict,
) -> None:
    body = json.dumps(payload, separators=(",", ":")).encode()
    writer.write(
        f"HTTP/1.1 {status}\r\n".encode()
        + b"Content-Type: application/json\r\n"
        + f"Content-Length: {len(body)}\r\n".encode()
        + b"Connection: close\r\n\r\n"
        + body
    )
    await writer.drain()
    writer.close()
    await writer.wait_closed()


async def _proxy_stream(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> None:
    try:
        while data := await reader.read(64 * 1024):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except ConnectionError:
            pass


async def _cdp_proxy_handler(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
) -> None:
    try:
        request_headers = await asyncio.wait_for(
            client_reader.readuntil(b"\r\n\r\n"),
            timeout=5,
        )
        lines = request_headers.split(b"\r\n")
        request_headers = b"\r\n".join(
            b"Host: localhost:9224"
            if line.lower().startswith(b"host:")
            else line
            for line in lines
        )
        upstream_reader, upstream_writer = await asyncio.open_connection(
            "127.0.0.1",
            9224,
        )
        upstream_writer.write(request_headers)
        await upstream_writer.drain()
    except (OSError, TimeoutError, asyncio.IncompleteReadError):
        client_writer.close()
        await client_writer.wait_closed()
        return
    await asyncio.gather(
        _proxy_stream(client_reader, upstream_writer),
        _proxy_stream(upstream_reader, client_writer),
    )


async def _control_handler(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    restart_event: asyncio.Event,
    process: asyncio.subprocess.Process,
) -> None:
    try:
        request_line = await asyncio.wait_for(reader.readline(), timeout=2)
        method, path, _ = request_line.decode(errors="replace").split(maxsplit=2)
        while True:
            line = await asyncio.wait_for(reader.readline(), timeout=2)
            if line in {b"\r\n", b"\n", b""}:
                break
        if method == "GET" and path == "/health":
            current = _read_int("/sys/fs/cgroup/pids.current")
            maximum = _read_int("/sys/fs/cgroup/pids.max")
            await _write_response(
                writer,
                "200 OK",
                {
                    "ok": process.returncode is None,
                    "pids_current": current,
                    "pids_max": maximum,
                },
            )
            return
        if method == "POST" and path == "/restart":
            await _write_response(writer, "202 Accepted", {"ok": True})
            restart_event.set()
            return
        await _write_response(writer, "404 Not Found", {"ok": False})
    except Exception:
        logger.debug("Browser control request failed", exc_info=True)
        writer.close()


async def _watch_resources(restart_event: asyncio.Event) -> None:
    threshold = min(
        95,
        max(50, int(os.getenv("ETSY_BROWSER_PID_THRESHOLD_PERCENT", "80"))),
    )
    consecutive_required = max(
        1,
        int(os.getenv("ETSY_BROWSER_PID_THRESHOLD_CHECKS", "3")),
    )
    interval = max(1.0, float(os.getenv("ETSY_BROWSER_WATCH_INTERVAL_SEC", "5")))
    high_count = 0
    initial_oom_kills = _oom_kill_count()
    while not restart_event.is_set():
        await asyncio.sleep(interval)
        current = _read_int("/sys/fs/cgroup/pids.current")
        maximum = _read_int("/sys/fs/cgroup/pids.max")
        if pid_threshold_reached(
            current,
            maximum,
            threshold_percent=threshold,
        ):
            high_count += 1
            logger.warning(
                "Browser PID usage high: %s/%s (%s/%s checks)",
                current,
                maximum,
                high_count,
                consecutive_required,
            )
        else:
            high_count = 0
        if high_count >= consecutive_required:
            logger.critical("Restarting browser container due to PID pressure")
            restart_event.set()
            return
        if _oom_kill_count() > initial_oom_kills:
            logger.critical("Restarting browser container after cgroup OOM kill")
            restart_event.set()
            return


async def main() -> None:
    logging.basicConfig(
        level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    restart_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, restart_event.set)

    process = await _start_chromium()
    async def control_handler(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        await _control_handler(
            reader,
            writer,
            restart_event,
            process,
        )

    server = await asyncio.start_server(
        control_handler,
        "0.0.0.0",
        9223,
    )
    cdp_proxy = await asyncio.start_server(
        _cdp_proxy_handler,
        "0.0.0.0",
        9222,
    )
    watchdog = asyncio.create_task(
        _watch_resources(restart_event),
        name="browser-resource-watchdog",
    )
    process_wait = asyncio.create_task(process.wait(), name="chromium-process")
    restart_wait = asyncio.create_task(restart_event.wait(), name="browser-restart")
    try:
        done, _ = await asyncio.wait(
            {process_wait, restart_wait},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if process_wait in done and not restart_event.is_set():
            raise RuntimeError(
                f"Chromium exited unexpectedly with code {process.returncode}"
            )
    finally:
        server.close()
        cdp_proxy.close()
        await server.wait_closed()
        await cdp_proxy.wait_closed()
        watchdog.cancel()
        process_wait.cancel()
        restart_wait.cancel()
        await _terminate_process_group(process)
        await asyncio.gather(
            watchdog,
            process_wait,
            restart_wait,
            return_exceptions=True,
        )
    logger.warning("Browser service exiting for clean container restart")


def run() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run()
