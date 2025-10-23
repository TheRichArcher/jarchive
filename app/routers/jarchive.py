import re
import requests
from bs4 import BeautifulSoup
from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/ja", tags=["jarchive"])

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

def _extract_title_text(soup: BeautifulSoup) -> str:
    # Try several known locations for the game title
    for sel in [
        "div#game_title h1",         # most common
        "div#game_title",            # sometimes not an h1
        "h1#game_title",             # rare
        "h1",                        # fallback
        "div#content h1",            # older templates
    ]:
        el = soup.select_one(sel)
        if el:
            t = el.get_text(" ", strip=True)
            if t:
                return t
    # Ultimate fallback: page title
    if soup.title and soup.title.string:
        return soup.title.get_text(" ", strip=True)
    return ""

def _parse_episode_meta(text: str):
    # Normalize whitespace and punctuation variants
    t = " ".join(text.split())

    # Try several formats seen across the archive over the years
    patterns = [
        r"Show #(\d+), aired (\d{4}-\d{2}-\d{2})",
        r"Show #(\d+)\s*-\s*(\d{4}-\d{2}-\d{2})",
        r"Show #(\d+)\s*\((\d{4}-\d{2}-\d{2})\)",
        r"Show #(\d+)\s*aired[: ]+(\d{4}-\d{2}-\d{2})",
        r"aired[: ]+(\d{4}-\d{2}-\d{2}).*Show #(\d+)",  # reversed order
    ]
    for pat in patterns:
        m = re.search(pat, t, flags=re.IGNORECASE)
        if m:
            # Ensure we always return as (episode_number, air_date)
            g1, g2 = m.group(1), m.group(2)
            if "aired" in pat and pat.startswith("aired"):
                # pattern with reversed order
                air_date, ep_num = g1, g2
            else:
                ep_num, air_date = g1, g2
            return ep_num, air_date

    # If we can’t find both, try to find them separately
    m_ep = re.search(r"Show #(\d+)", t, flags=re.IGNORECASE)
    m_dt = re.search(r"(\d{4}-\d{2}-\d{2})", t)
    if m_ep and m_dt:
        return m_ep.group(1), m_dt.group(1)

    return None, None

def parse_episode(url: str) -> dict:
    r = requests.get(url, headers=HEADERS, timeout=30)
    if r.status_code != 200:
        raise HTTPException(status_code=400, detail=f"Could not fetch episode page: {r.status_code}")
    soup = BeautifulSoup(r.text, "html.parser")

    title_text = _extract_title_text(soup)
    ep_num, air_date = _parse_episode_meta(title_text)

    if not (ep_num and air_date):
        # Try one more fallback: sometimes a small header block carries metadata
        header_blk = soup.select_one("#game_title") or soup.select_one("#content")
        if header_blk:
            ep_num, air_date = _parse_episode_meta(header_blk.get_text(" ", strip=True))

    if not (ep_num and air_date):
        raise HTTPException(status_code=400, detail="Episode number/air date not found")

    return {
        "episode_number": ep_num,
        "air_date": air_date,
        "html": r.text,
        "url": url,
    }

def extract_category_items(html: str, category_title: str):
    soup = BeautifulSoup(html, "html.parser")

    def extract_from_round(round_id: str):
        tbl = soup.select_one(f"table#{round_id}")
        if not tbl:
            return None
        categories = [c.get_text(strip=True) for c in tbl.select("td.category_name")]
        if category_title not in categories:
            return None
        col_idx = categories.index(category_title)

        # Collect clue rows and capture the cell for our category column
        clue_rows = [r for r in tbl.select("tr") if r.select_one("td.clue")]
        items = []
        for r in clue_rows:
            clue_cells = r.select("td.clue")
            if len(clue_cells) < 6:
                continue
            cell = clue_cells[col_idx]

            # clue text
            clue_text_el = cell.select_one(".clue_text, td.clue_text, div.clue_text")
            clue_text = clue_text_el.get_text(" ", strip=True) if clue_text_el else ""

            # correct response
            correct_el = cell.select_one(".correct_response, em.correct_response, div em")
            answer_exact = correct_el.get_text(" ", strip=True) if correct_el else ""

            # fallback: legacy onmouseover HTML contains <em>correct response</em>
            if not answer_exact:
                mouseover = cell.select_one("[onmouseover]")
                if mouseover:
                    try:
                        inner = BeautifulSoup(mouseover.get("onmouseover"), "html.parser")
                        em = inner.find("em")
                        if em:
                            answer_exact = em.get_text(" ", strip=True)
                    except Exception:
                        pass

            if clue_text or answer_exact:
                items.append({"clue_raw": clue_text, "answer_exact": answer_exact})

        # Enforce five items in standard board order
        norm_values = ["$200", "$400", "$600", "$800", "$1000"]
        result = []
        for i, it in enumerate(items[:5]):
            result.append({"value": norm_values[i], **it})
        return result if len(result) == 5 else None

    for rid in ["jeopardy_round", "double_jeopardy_round"]:
        items = extract_from_round(rid)
        if items:
            return items
    return None

@router.get("/extract")
def extract(
    episode_url: str = Query(..., description="Full J! Archive episode URL"),
    category: str = Query(..., description="Exact category title as shown on the board"),
):
    ep = parse_episode(episode_url)
    items = extract_category_items(ep["html"], category)
    if not items or any(not x["clue_raw"] or not x["answer_exact"] for x in items):
        raise HTTPException(status_code=422, detail="Could not extract five verified items for that category.")
    return {
        "episode_air_date": ep["air_date"],
        "episode_number": ep["episode_number"],
        "category": category,
        "items": items,
        "source": "J! Archive",
        "episode_url": ep["url"],
        "notes": "",
    }


