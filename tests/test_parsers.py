from kipu.ingest.html_md import html_to_markdown
from kipu.ingest.parsers import _xml_rows, humanize_title, parse_markdown


def test_humanize_title_matches_sample() -> None:
    assert humanize_title("memo_cierre_julio.md") == "Memo cierre de julio"
    assert humanize_title("faq_clientes.html") == "Faq clientes"


def test_markdown_front_matter_is_metadata_and_kept_in_body() -> None:
    text = "---\ntitle: Memo\nauthor: kelly@kipupay.co\n---\n# Memo\n"
    body, metadata = parse_markdown(text)
    assert body == text
    assert metadata == {"title": "Memo", "author": "kelly@kipupay.co"}


def test_html_keeps_meta_and_comments() -> None:
    html = (
        '<html><head><meta name="author" content="Adrián Quintero"><title>T</title></head>'
        "<body><h1>Hola</h1><p>Texto <a href='mailto:a@b.co'>a@b.co</a></p>"
        "<table><tr><th>A</th></tr><tr><td>1</td></tr></table><!-- por x@y.co --></body></html>"
    )
    body, metadata = html_to_markdown(html)
    assert metadata == {"author": "Adrián Quintero"}
    assert '<meta name="author" content="Adrián Quintero">' in body
    assert "# Hola" in body and "Texto a@b.co" in body and "| A |" in body
    assert "<!-- por x@y.co -->" in body


def test_xml_rows_keep_attributes_and_children() -> None:
    xml = (
        b'<r><e id="KP-1" ciudad="Cali"><nombre>Ana</nombre>'
        b'<documento tipo="CC">1</documento></e></r>'
    )
    assert _xml_rows(xml) == [
        {"id": "KP-1", "ciudad": "Cali", "nombre": "Ana", "documento": "1", "documento_tipo": "CC"}
    ]
