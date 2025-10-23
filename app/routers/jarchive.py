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

    diag_root = {"rounds": []} if debug else None

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
            # Prefer classed correct_response
            m = re.search(
                r'<em[^>]*class=["\']?correct_response["\']?[^>]*>(.*?)</em>',
                unescaped,
                flags=re.IGNORECASE | re.DOTALL,
            )
            if m:
                return BeautifulSoup(m.group(1), "html.parser").get_text(" ", strip=True)
            # Fallback: any <em>...</em>
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
        round_diag = {"round": round_id}
        if not tbl:
            if debug:
                round_diag.update({"found": False, "reason": "table not found"})
                diag_root["rounds"].append(round_diag)
            return None

        # Map round id to clue id prefix and expected values
        if round_id == "double_jeopardy_round":
            prefix = "DJ"
            norm_values = ["$400", "$800", "$1200", "$1600", "$2000"]
        else:
            prefix = "J"
            norm_values = ["$200", "$400", "$600", "$800", "$1000"]

        raw_categories = [c.get_text(" ", strip=True) for c in tbl.select("td.category_name")]
        cats_norm = [_norm_cat(c) for c in raw_categories]
        target = _norm_cat(category_title)
        if target not in cats_norm:
            if debug:
                round_diag.update({"found": False, "reason": "category not present", "categories": cats_norm})
                diag_root["rounds"].append(round_diag)
            return None
        col_idx = cats_norm.index(target)          # 0-based index into categories
        col_num = col_idx + 1                      # 1..6 for clue ids
        if debug:
            round_diag.update({"found": True, "col_index": col_idx, "rows": []})

        items = []
        # Rows on the board are numbered top-to-bottom 1..5
        for row_num in range(1, 6):
            row_diag = {"row": row_num} if debug else None
            # Try the canonical per-clue cell id
            cell = soup.select_one(f'td#clue_{prefix}_{col_num}_{row_num}')
            if not cell:
                # Some legacy templates wrap with a div with that id; try that too
                cell = soup.select_one(f'div#clue_{prefix}_{col_num}_{row_num}')
                # If it's a div container, move to its closest td for onmouseover, etc.
                if cell and cell.name == "div":
                    parent_td = cell.find_parent("td")
                    cell = parent_td or cell
            if debug:
                row_diag and row_diag.update({"has_cell": bool(cell)})

            if not cell:
                if debug:
                    row_diag and row_diag.update({"reason": "missing clue cell"})
                    round_diag["rows"].append(row_diag)
                    round_diag["failed_row"] = row_num
                    diag_root["rounds"].append(round_diag)
                return None

            # Clue text: prefer .clue_text; otherwise the cell text minus value label
            clue_text_el = cell.select_one(".clue_text, td.clue_text, div.clue_text")
            clue_raw = _extract_text(clue_text_el)
            if not clue_raw:
                cand = cell.get_text(" ", strip=True)
                for v in ["$200", "$400", "$600", "$800", "$1000", "$1200", "$1600", "$2000"]:
                    cand = cand.replace(v, "").strip()
                clue_raw = cand
            if debug:
                row_diag and row_diag.update({"has_clue": bool(clue_raw)})

            # Correct response: modern first, then legacy onmouseover
            answer_exact = ""
            correct_el = cell.select_one(".correct_response, em.correct_response")
            if correct_el:
                answer_exact = _extract_text(correct_el)
                used = "modern"
            else:
                used = None

            if not answer_exact:
                info_div = soup.select_one(f'div#clue_{prefix}_{col_num}_{row_num}')
                if info_div:
                    correct_el2 = info_div.select_one(".correct_response, em.correct_response")
                    if correct_el2:
                        answer_exact = _extract_text(correct_el2)
                        used = used or "info_div"

            if not answer_exact:
                holder = cell if cell.has_attr("onmouseover") else cell.select_one("[onmouseover]")
                if holder:
                    answer_exact = _decode_onmouseover(holder.get("onmouseover", ""))
                    used = used or "onmouseover"

            if debug:
                row_diag and row_diag.update({"has_answer": bool(answer_exact), "answer_source": used})

            if not clue_raw or not answer_exact:
                if debug:
                    row_diag and row_diag.update({"reason": "missing clue or answer"})
                    round_diag["rows"].append(row_diag)
                    round_diag["failed_row"] = row_num
                    diag_root["rounds"].append(round_diag)
                return None

            items.append({
                "value": norm_values[row_num - 1],
                "clue_raw": clue_raw,
                "answer_exact": answer_exact,
            })
            if debug:
                round_diag["rows"].append(row_diag)

        if len(items) != 5:
            if debug:
                round_diag["reason"] = "not exactly five items"
                diag_root["rounds"].append(round_diag)
            return None

        if debug:
            round_diag["success"] = True
            diag_root["rounds"].append(round_diag)
        return items

    # Try Jeopardy, then Double Jeopardy
    for rid in ["jeopardy_round", "double_jeopardy_round"]:
        res = extract_from_round(rid)
        if res:
            return res, (diag_root if debug else None)
    return None, (diag_root if debug else None)

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


