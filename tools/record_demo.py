"""Record the README demo (docs/demo.gif) by driving ReaderHelper in a headless browser.

Setup (separate from the app's requirements):
    pip install playwright imageio-ffmpeg && python -m playwright install chromium

Use a separate data folder with only public-domain books, e.g. Alice in Wonderland and Die Verwandlung
from Project Gutenberg in <demo>/books/, and start the server on port 8010:
    DATA_DIR=<demo> uvicorn backend.main:app --port 8010

Run once without --record: the local model is slow, so this fills the lookup cache and saves screenshots
to check. Then delete the saved words (Vocabulary page) and run with --record for the fast, cached run:
    python tools/record_demo.py <out-dir> [--record]
Turn the .webm in <out-dir>/video into a GIF with ffmpeg (1.6x speed, 960 px, 10 fps, 128-colour palette)."""
import json
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8010"
RECORD = "--record" in sys.argv
OUT = Path(sys.argv[1])
W, H = 1280, 760

CURSOR_JS = """
if (window.top === window) addEventListener('DOMContentLoaded', () => {
  const c = document.createElement('div');
  c.id = '__cursor';
  c.style.cssText = 'position:fixed;z-index:2147483647;left:640px;top:400px;width:22px;height:22px;' +
    'margin:-11px 0 0 -11px;border-radius:50%;background:rgba(220,60,60,.35);border:2px solid rgba(200,40,40,.9);' +
    'pointer-events:none;transition:left .55s ease, top .55s ease, transform .15s';
  document.body.appendChild(c);
});
"""


def api(path, data=None):
    req = urllib.request.Request(BASE + path, data=json.dumps(data).encode() if data else None,
                                 headers={"content-type": "application/json"})
    return json.load(urllib.request.urlopen(req))


def pause(s):
    time.sleep(s if RECORD else min(s, 0.3))


def move_click(page, x, y):
    page.evaluate("([x, y]) => { const c = document.getElementById('__cursor'); if (c) { c.style.left = x + 'px'; c.style.top = y + 'px'; } }", [x, y])
    time.sleep(0.65)
    page.evaluate("() => { const c = document.getElementById('__cursor'); if (c) { c.style.transform = 'scale(.6)'; setTimeout(() => c.style.transform = '', 160); } }")
    page.mouse.click(x, y)


def click_el(page, selector):
    el = page.locator(selector).first
    el.evaluate("e => e.scrollIntoView({block: 'nearest', behavior: 'smooth'})")
    time.sleep(0.7)
    box = el.bounding_box()
    move_click(page, box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)


FIND_WORD = """(word) => {
  const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const re = new RegExp('(^|[^\\\\p{L}])' + word + '(?![\\\\p{L}])', 'u');
  const out = [];
  for (let n; (n = w.nextNode());) {
    const m = re.exec(n.data);
    if (!m) continue;
    const r = document.createRange();
    const start = m.index + m[1].length;
    r.setStart(n, start); r.setEnd(n, start + word.length);
    const b = r.getBoundingClientRect();
    out.push({x: b.left + b.width / 2, y: b.top + b.height / 2});
  }
  return out;
}"""


def click_word(page, word):
    """Click `word` where it is visible on the current page of the EPUB iframe."""
    view = page.locator("#viewer").bounding_box()
    for frame in page.frames[1:]:
        el = frame.frame_element()
        fb = el.bounding_box()
        if not fb:
            continue
        for p in frame.evaluate(FIND_WORD, word):
            x, y = fb["x"] + p["x"], fb["y"] + p["y"]
            if view["x"] < x < view["x"] + view["width"] and view["y"] < y < view["y"] + view["height"]:
                move_click(page, x, y)
                return
    raise RuntimeError(f"{word!r} not visible")


def wait_popover(page, timeout=180):
    """Until the popover has finished loading (cached answers are instant)."""
    t0 = time.time()
    page.wait_for_selector("#popover:not([hidden])")
    while time.time() - t0 < timeout:
        if page.locator("#popover .loading").count() == 0 and page.locator("#popover button.save").count():
            return
        time.sleep(0.3)
    raise RuntimeError("popover still loading")


def open_chapter(page, book_id, chapter):
    page.goto(f"{BASE}/read?book={book_id}")
    page.wait_for_selector("#tocBtn")
    pause(1.0)
    time.sleep(2)  # epub.js needs a moment
    click_el(page, "#tocBtn")
    pause(0.6)
    click_el(page, f"#tocList .toc-item:text-is('{chapter}')")
    time.sleep(2)


def lookup(page, word, shot, ask=False):
    click_word(page, word)
    wait_popover(page)
    pause(2.0)
    if ask and page.locator("#popover button[data-act=ask]").count():
        click_el(page, "#popover button[data-act=ask]")
        time.sleep(0.4)
        wait_popover(page, timeout=300)
    if ask:
        pause(3.2)
    page.screenshot(path=OUT / f"{shot}{'_rec' if RECORD else ''}.png")
    if not RECORD:
        print(word, "->", page.inner_text("#popover")[:300].replace("\n", " | "))
    save = page.locator("#popover button.save[data-act=save]")
    if save.count():
        click_el(page, "#popover button.save[data-act=save]")
        pause(1.2)
    page.keyboard.press("Escape")
    pause(0.4)


profile = api("/api/profiles")[0]
books = {b["language"][:2]: b["id"] for b in api("/api/books")}

with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(viewport={"width": W, "height": H}, device_scale_factor=1,
                              record_video_dir=str(OUT / "video") if RECORD else None,
                              record_video_size={"width": W, "height": H})
    ctx.add_init_script(f"localStorage.setItem('profile', JSON.stringify({json.dumps(profile)}))")
    ctx.add_init_script(CURSOR_JS)
    page = ctx.new_page()

    page.goto(BASE + "/")
    page.wait_for_selector(".book-open")
    pause(1.8)
    click_el(page, f"a.book-open[href='/read?book={books['en']}']")
    page.wait_for_selector("#tocBtn")
    time.sleep(2.5)
    click_el(page, "#tocBtn")
    pause(0.8)
    click_el(page, "#tocList .toc-item:text-is('CHAPTER I. Down the Rabbit-Hole')")
    time.sleep(2)
    pause(0.8)
    lookup(page, "bank", "en_bank", ask=True)

    open_chapter(page, books["de"], "I.")
    pause(1.0)
    lookup(page, "erwachte", "de_erwachte")
    lookup(page, "verwandelt", "de_verwandelt")

    click_el(page, "a[href='/vocab']")
    page.wait_for_selector("#rows tr")
    pause(2.5)
    if not RECORD:
        page.screenshot(path=OUT / "vocab.png")

    page.goto(BASE + "/review")
    page.wait_for_selector("#showBtn:visible")
    pause(1.5)
    click_el(page, "#showBtn")
    pause(2.2)
    if not RECORD:
        page.screenshot(path=OUT / "review.png")
    click_el(page, "button[data-grade=good]")
    pause(1.5)

    ctx.close()
    browser.close()
