from bot.services.taxonomy_parsers import (
    extract_ebay_category_id,
    parse_ebay_all_categories_html,
    parse_ebay_subcategory_html,
    parse_etsy_categories_html,
    parse_poshmark_category_hrefs,
    poshmark_nodes_from_slugs,
)


EBAY_HTML = """
<html><body>
<ul class="cats">
  <li>
    <a href="/b/Clothing-Shoes-Accessories/11450/bn_1">Clothing, Shoes &amp; Accessories</a>
    <ul>
      <li><a href="https://www.ebay.com/sch/i.html?_sacat=15724">Women</a></li>
      <li><a href="/sch/1059/i.html">Men</a></li>
    </ul>
  </li>
  <li><a href="/sch/281/i.html">Jewelry &amp; Watches</a></li>
</ul>
</body></html>
"""

ETSY_HTML = """
<html><body>
<a href="/c/jewelry?taxonomy_id=10">Jewelry</a>
<a href="/c/jewelry/bracelets?taxonomy_id=1200">Bracelets</a>
<a href="/c/home-and-living">Home &amp; Living</a>
</body></html>
"""

POSH_HTML = """
<html><body>
<a href="/category/Women">Women</a>
<a href="/category/Women-Bags">Bags</a>
<a href="/category/Women-Bags-Crossbody_Bags">Crossbody Bags</a>
<a href="/category/Men-Shoes">Shoes</a>
</body></html>
"""


def test_extract_ebay_category_id():
    assert extract_ebay_category_id("/sch/11450/i.html") == "11450"
    assert extract_ebay_category_id("https://ebay.com/sch/i.html?_nkw=x&_sacat=15724") == "15724"
    assert extract_ebay_category_id("/b/Clothing/11450/bn_1") == "11450"


def test_parse_ebay_all_categories_html():
    nodes = parse_ebay_all_categories_html(EBAY_HTML, host="www.ebay.com")
    by_id = {n["id"]: n for n in nodes}
    assert "11450" in by_id
    assert "15724" in by_id
    assert by_id["15724"]["parent_id"] == "11450"
    assert "Women" in by_id["15724"]["path"]


def test_parse_ebay_subcategory_html():
    html = '<a href="/sch/i.html?_sacat=4250">Shoes</a>'
    nodes = parse_ebay_subcategory_html(
        html, parent_id="11450", parent_path="Clothing"
    )
    assert nodes[0]["id"] == "4250"
    assert nodes[0]["parent_id"] == "11450"


def test_parse_etsy_categories_html():
    nodes = parse_etsy_categories_html(ETSY_HTML)
    by_id = {n["id"]: n for n in nodes}
    assert by_id["10"]["meta"]["taxonomy_id"] == 10
    assert by_id["1200"]["meta"]["taxonomy_id"] == 1200
    assert any((n.get("meta") or {}).get("slug") == "home-and-living" for n in nodes)


def test_poshmark_slug_tree():
    slugs = parse_poshmark_category_hrefs(POSH_HTML)
    nodes = poshmark_nodes_from_slugs(slugs)
    names = {n["name"] for n in nodes}
    assert "Women" in names
    assert "Crossbody Bags" in names
    assert any(n["id"].startswith("s:Women|Bags|") for n in nodes)
