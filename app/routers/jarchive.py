import re
from datetime import datetime
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
        "div#game_title h1",
        "div#game_title",
        "h1#game_title",
        "div#content h1",
        "h1",
        "title",
    ]:
        el = soup.select_one(sel)
        if el:
            t = el.get_text(" ", strip=True)
            if t:
                return t
    return ""

MONTH_NAME_PAT = r"(January|February|March|April|May|June|July|August|September|October|November|December)"
TEXTUAL_DATE_PAT = rf"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday,\s*)?{MONTH_NAME_PAT}\s+\d{{1,2}},\s+\d{{4}}"

def _to_iso_date(date_str: str) -> str | None:
    """Normalize either YYYY-MM-DD or 'Month D, YYYY' to YYYY-MM-DD."""
    s = " ".join(date_str.strip().split())
    # Already ISO?
    m = re.fullmatch(r"\d{4}-\d{2}-\d{2}", s)
    if m:
        return s
    # Try textual dates like 'Wednesday, March 6, 2013' or 'March 6, 2013'
    for fmt in ["%A, %B %d, %Y", "%B %d, %Y"]:
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return None

def _parse_episode_meta(text: str):
    t = " ".join(text.split())

    # 1) Direct ISO patterns
    patterns_iso = [
        r"Show #(\d+), aired (\d{4}-\d{2}-\d{2})",
        r"Show #(\d+)\s*-\s*(\d{4}-\d{2}-\d{2})",
        r"Show #(\d+)\s*\((\d{4}-\d{2}-\d{2})\)",
        r"Show #(\d+)\s*aired[: ]+(\d{4}-\d{2}-\d{2})",
        r"aired[: ]+(\d{4}-\d{2}-\d{2}).*Show #(\d+)",
    ]
    for pat in patterns_iso:
        m = re.search(pat, t, flags=re.IGNORECASE)
        if m:
            g1, g2 = m.group(1), m.group(2)
            if pat.startswith("aired"):
                air_date_iso, ep_num = _to_iso_date(g1), g2
            else:
                ep_num, air_date_iso = g1, _to_iso_date(g2)
            if air_date_iso:
                return ep_num, air_date_iso

    # 2) Textual date patterns
    pat_textual = rf"Show #(\d+)[\s\-–—:]*({TEXTUAL_DATE_PAT})"
    m = re.search(pat_textual, t, flags=re.IGNORECASE)
    if m:
        ep_num, date_str = m.group(1), m.group(2)
        air_date_iso = _to_iso_date(date_str)
        if air_date_iso:
            return ep_num, air_date_iso

    # 3) Last resort: find ep and any date separately
    m_ep = re.search(r"Show #(\d+)", t, flags=re.IGNORECASE)
    m_dt_iso = re.search(r"(\d{4}-\d{2}-\d{2})", t)
    m_dt_text = re.search(TEXTUAL_DATE_PAT, t, flags=re.IGNORECASE)
    if m_ep and (m_dt_iso or m_dt_text):
        ep = m_ep.group(1)
        date_str = (m_dt_iso or m_dt_text).group(0)
        air_date_iso = _to_iso_date(date_str)
        if air_date_iso:
            return ep, air_date_iso

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

def extract_category_items(html_text: str, category_title: str, debug: bool = False):
    soup = BeautifulSoup(html_text, "html.parser")
    diag = {"round_tried": [], "matched_round": None, "target_category": category_title, "col_idx": None, "rows": []}

    def log(**kv):
        if debug:
            diag["rows"].append(kv)

    # ---------- helpers ----------
    def _norm_cat(s: str) -> str:
        if not s:
            return ""
        s = html.unescape(s)
        s = s.replace("&", "and")
        s = re.sub(r"\s+", " ", s).strip()
        return s.upper()

    def _decode_onmouseover(attr_val: str) -> str:
        if not attr_val:
            return ""
        try:
            unescaped = html.unescape(attr_val)
            m = re.search(
                r'<em[^>]*class=["\']?correct_response["\']?[^>]*>(.*?)</em>',
                unescaped,
                flags=re.IGNORECASE | re.DOTALL,
            )
            if m:
                return BeautifulSoup(m.group(1), "html.parser").get_text(" ", strip=True)
            m2 = re.search(r"<em[^>]*>(.*?)</em>", unescaped, flags=re.IGNORECASE | re.DOTALL)
            if m2:
                return BeautifulSoup(m2.group(1), "html.parser").get_text(" ", strip=True)
        except Exception:
            pass
        return ""

    def _extract_text(el):
        return el.get_text(" ", strip=True) if el else ""

    # ---------- round extractor using clue ids ----------
    def extract_from_round(round_id: str):
        tbl = soup.select_one(f"table#{round_id}")
        diag["round_tried"].append(round_id)
        if not tbl:
            return None

        if round_id == "double_jeopardy_round":
            prefix = "DJ"; norm_values = ["$400", "$800", "$1200", "$1600", "$2000"]
        else:
            prefix = "J";  norm_values = ["$200", "$400", "$600", "$800", "$1000"]

        raw_categories = [c.get_text(" ", strip=True) for c in tbl.select("td.category_name")]
        cats_norm = [_norm_cat(c) for c in raw_categories]
        target = _norm_cat(category_title)
        if target not in cats_norm:
            return None
        col_idx = cats_norm.index(target)     # 0-based
        col_num = col_idx + 1
        diag["matched_round"] = round_id
        diag["col_idx"] = col_idx

        items = []
        for row_num in range(1, 6):
            cell = soup.select_one(f'td#clue_{prefix}_{col_num}_{row_num}') \
                   or soup.select_one(f'div#clue_{prefix}_{col_num}_{row_num}') \
                   or None
            if cell and cell.name == "div":
                cell = cell.find_parent("td") or cell

            if not cell:
                log(prefix=prefix, row=row_num, step="no_cell", clue_id=f"clue_{prefix}_{col_num}_{row_num}")
                return None

            clue_el = cell.select_one(".clue_text, td.clue_text, div.clue_text")
            clue_raw = _extract_text(clue_el)
            if not clue_raw:
                cand = cell.get_text(" ", strip=True)
                for v in ["$200", "$400", "$600", "$800", "$1000", "$1200", "$1600", "$2000"]:
                    cand = cand.replace(v, "").strip()
                clue_raw = cand

            answer_exact = ""
            correct_el = cell.select_one(".correct_response, em.correct_response")
            source = None
            if correct_el:
                answer_exact = _extract_text(correct_el); source = "in_cell.correct_response"

            if not answer_exact:
                info_div = soup.select_one(f'div#clue_{prefix}_{col_num}_{row_num}')
                if info_div:
                    correct_el2 = info_div.select_one(".correct_response, em.correct_response")
                    if correct_el2:
                        answer_exact = _extract_text(correct_el2); source = "info_div.correct_response"

            if not answer_exact:
                holder = cell if cell.has_attr("onmouseover") else cell.select_one("[onmouseover]")
                if holder:
                    answer_exact = _decode_onmouseover(holder.get("onmouseover", "")); source = "onmouseover"

            log(prefix=prefix, row=row_num, clue_id=f"clue_{prefix}_{col_num}_{row_num}",
                clue_ok=bool(clue_raw), ans_ok=bool(answer_exact), source=source,
                clue_preview=clue_raw[:120], answer_preview=answer_exact[:120])

            if not clue_raw or not answer_exact:
                return None

            items.append({"value": norm_values[row_num - 1], "clue_raw": clue_raw, "answer_exact": answer_exact})

        return items

    for rid in ["jeopardy_round", "double_jeopardy_round"]:
        items = extract_from_round(rid)
        if items:
            return items, diag if debug else None
    return None, diag if debug else None

@router.get("/extract")
def ja_extract(episode_url: str = Query(...), category: str = Query(...), debug: bool = Query(False)):
    ep = parse_episode(episode_url)
    items, diag = extract_category_items(ep["html"], category, debug=debug)
    if not items:
        raise HTTPException(status_code=422, detail="Could not extract five verified items for that category.", headers={"X-Debug": "1" if debug else "0"})
    return {
        "episode_air_date": ep["air_date"],
        "episode_number": ep["episode_number"],
        "category": category,
        "items": items,
        "debug": diag if debug else None,
    }


