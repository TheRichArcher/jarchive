import re
import html
import html as _html
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

def _norm_cat(s: str) -> str:
    if not s:
        return ""
    s = _html.unescape(s)
    s = s.replace("&", "and")
    s = re.sub(r"\s+", " ", s).strip()
    return s.upper()

def extract_category_items(html_text: str, category_title: str):
    soup = BeautifulSoup(html_text, "html.parser")
    target = _norm_cat(category_title)

    def parse_correct_response_from_onmouseover(attr_val: str) -> str:
        """
        Older J! Archive stores a small HTML snippet in the onmouseover attribute.
        Example contains: <em class="correct_response">ANSWER</em>
        We unescape and then regex out the inner text.
        """
        if not attr_val:
            return ""
        try:
            unescaped = html.unescape(attr_val)
            # Common patterns: <em class="correct_response">X</em> or <em>X</em>
            m = re.search(r'<em[^>]*class=["\']?correct_response["\']?[^>]*>(.*?)</em>', unescaped, flags=re.IGNORECASE|re.DOTALL)
            if m:
                return BeautifulSoup(m.group(1), "html.parser").get_text(" ", strip=True)
            # Fallback: any <em>…</em>
            m2 = re.search(r'<em[^>]*>(.*?)</em>', unescaped, flags=re.IGNORECASE|re.DOTALL)
            if m2:
                return BeautifulSoup(m2.group(1), "html.parser").get_text(" ", strip=True)
        except Exception:
            pass
        return ""

    def extract_from_round(round_id: str):
        tbl = soup.select_one(f"table#{round_id}")
        if not tbl:
            return None

        # Columns (categories) at top of the table
        raw_categories = [c.get_text(" ", strip=True) for c in tbl.select("td.category_name")]
        categories_norm = [_norm_cat(c) for c in raw_categories]
        if target not in categories_norm:
            return None
        col_idx = categories_norm.index(target)

        # Determine expected board values by round
        if round_id == "double_jeopardy_round":
            norm_values = ["$400", "$800", "$1200", "$1600", "$2000"]
        else:
            norm_values = ["$200", "$400", "$600", "$800", "$1000"]

        # Each row with clues has 6 td.clue cells (one per category column).
        # Some cells may be missing; we keep placeholders and only count valid ones.
        items = []
        for r in tbl.select("tr"):
            cells = r.select("td.clue")
            if len(cells) < 6:
                continue
            cell = cells[col_idx]

            # Clue text
            clue_text_el = cell.select_one(".clue_text, td.clue_text, div.clue_text")
            clue_text = clue_text_el.get_text(" ", strip=True) if clue_text_el else ""

            # Correct response: modern first, legacy fallback
            answer_exact = ""
            correct_el = cell.select_one(".correct_response, em.correct_response")
            if correct_el:
                answer_exact = correct_el.get_text(" ", strip=True)

            if not answer_exact:
                # Legacy: attribute contains mini HTML
                # Often on the <td class="clue"> itself or on a <div> child
                onmouseover_holder = cell if cell.has_attr("onmouseover") else cell.select_one("[onmouseover]")
                if onmouseover_holder:
                    answer_exact = parse_correct_response_from_onmouseover(onmouseover_holder.get("onmouseover", ""))

            # Only append if we found at least clue or answer (some cells are empty)
            if clue_text or answer_exact:
                items.append({"clue_raw": clue_text, "answer_exact": answer_exact})

            # Stop early if we already have 5
            if len(items) >= 5:
                break

        # Enforce exactly five items in board (top-to-bottom) order for that column
        if len(items) < 5:
            return None
        result = []
        for i in range(5):
            it = items[i]
            result.append({
                "value": norm_values[i],
                "clue_raw": it.get("clue_raw", ""),
                "answer_exact": it.get("answer_exact", ""),
            })
        # Require both clue_raw and answer_exact for all five
        if any(not x["clue_raw"] or not x["answer_exact"] for x in result):
            return None
        return result

    # Try Jeopardy then Double Jeopardy
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


