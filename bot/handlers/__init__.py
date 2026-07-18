from aiogram import Router

from bot.handlers import admin, common, searches, setup, start


def build_root_router() -> Router:
    root = Router(name="root")
    root.include_router(start.router)
    root.include_router(admin.router)
    root.include_router(setup.router)
    root.include_router(searches.router)
    root.include_router(common.router)
    return root
