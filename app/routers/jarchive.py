import re
import requests
from bs4 import BeautifulSoup
from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/ja", tags=["jarchive"])

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; Woo-Combine/1.0)"}

def parse_episode(url: str) -> dict:
    r = requests.get(url, headers=HEADERS, timeout=30)
    if r.status_code != 200:
        raise HTTPException(status_code=400, detail=f"Could not fetch episode page: {r.status_code}")
    soup = BeautifulSoup(r.text, "html.parser")

    title_el = soup.select_one("div#game_title h1")
    if not title_el:
        raise HTTPException(status_code=400, detail="Episode header not found")
    title_text = title_el.get_text(" ", strip=True)
    m = re.search(r"Show #(\d+), aired (\d{4}-\d{2}-\d{2})", title_text)
    if not m:
        raise HTTPException(status_code=400, detail="Episode number/air date not found")
    episode_number, air_date = m.group(1), m.group(2)

    return {
        "episode_number": episode_number,
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


