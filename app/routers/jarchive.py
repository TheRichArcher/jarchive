from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import re
import requests
from bs4 import BeautifulSoup
from fastapi import APIRouter, HTTPException, Query


router = APIRouter()


def _extract_episode_meta(soup: BeautifulSoup) -> Tuple[Optional[str], Optional[str]]:
    # J! Archive typically has title like: "Show #1234 - Thursday, January 1, 2000"
    title_el = soup.find("title")
    if not title_el or not title_el.text:
        return None, None
    title_text = title_el.text.strip()

    # Episode number
    ep_num_match = re.search(r"Show\s*#\s*(\d+)", title_text)
    episode_number = ep_num_match.group(1) if ep_num_match else None

    # Air date: try to parse month day, year
    # Extract substring that looks like Month Day, Year
    date_match = re.search(r"([A-Za-z]+\s+\d{1,2},\s+\d{4})", title_text)
    air_date: Optional[str] = None
    if date_match:
        try:
            dt = datetime.strptime(date_match.group(1), "%B %d, %Y")
            air_date = dt.strftime("%Y-%m-%d")
        except Exception:
            air_date = None

    return air_date, episode_number


def _normalize_text(text: str) -> str:
    # Keep verbatim but strip surrounding whitespace and normalize internal whitespace to single spaces
    return re.sub(r"\s+", " ", text).strip()


def _find_category_round_and_index(soup: BeautifulSoup, target_category: str) -> Optional[Tuple[str, int]]:
    # Search Jeopardy round
    j_round = soup.select_one("#jeopardy_round")
    if j_round:
        cats = j_round.select(".category")
        for idx, cat in enumerate(cats):
            name_el = cat.select_one(".category_name")
            if not name_el:
                continue
            name = _normalize_text(name_el.get_text(" "))
            if name.lower() == target_category.lower():
                return ("J", idx)

    # Search Double Jeopardy round
    dj_round = soup.select_one("#double_jeopardy_round")
    if dj_round:
        cats = dj_round.select(".category")
        for idx, cat in enumerate(cats):
            name_el = cat.select_one(".category_name")
            if not name_el:
                continue
            name = _normalize_text(name_el.get_text(" "))
            if name.lower() == target_category.lower():
                return ("DJ", idx)

    return None


def _extract_category_clues_for_round(soup: BeautifulSoup, round_code: str, col_index: int) -> List[Dict[str, str]]:
    items: List[Dict[str, str]] = []
    # Rows are 1..5 top to bottom
    for row in range(1, 6):
        clue_id = f"clue_{round_code}_{col_index + 1}_{row}"
        cell = soup.select_one(f"td.clue#{clue_id}")
        if not cell:
            # Some archived games might omit the id on td but have a div with the id
            cell = soup.select_one(f"div#{clue_id}")
        if not cell:
            continue

        clue_text_el = cell.select_one(".clue_text")
        answer_el = cell.select_one(".correct_response")
        if not clue_text_el or not answer_el:
            # Try to locate answer via nested div with id
            if not answer_el:
                answer_el = soup.select_one(f"div#{clue_id} .correct_response")
        if not clue_text_el or not answer_el:
            continue

        clue_raw = _normalize_text(clue_text_el.get_text(" "))
        answer_exact = _normalize_text(answer_el.get_text(" "))

        value_el = cell.select_one(".clue_value, .clue_value_daily_double")
        value: Optional[str] = None
        if value_el and value_el.text:
            val_txt = value_el.text.strip().replace(",", "")
            m_val = re.search(r"\$\d+", val_txt)
            if m_val:
                value = m_val.group(0)

        # Fallback value by row and round if not present
        if not value:
            base = 200 if round_code == "J" else 400
            value = f"${base * row}"

        items.append({
            "value": value,
            "clue_raw": clue_raw,
            "answer_exact": answer_exact,
        })

    return items


def _extract_category_clues(soup: BeautifulSoup, target_category: str) -> List[Dict[str, str]]:
    found = _find_category_round_and_index(soup, target_category)
    if not found:
        return []
    round_code, col_index = found
    items = _extract_category_clues_for_round(soup, round_code, col_index)
    # Ensure exactly five complete items
    items = [i for i in items if i.get("clue_raw") and i.get("answer_exact")]
    if len(items) > 5:
        items = items[:5]
    return items


@router.get("/extract")
def extract_from_jarchive(
    episode_url: str = Query(..., description="J! Archive episode URL"),
    category: str = Query(..., description="Category name to extract"),
) -> Dict[str, Any]:
    try:
        resp = requests.get(episode_url, timeout=20)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Failed to fetch episode URL: {e}")

    if resp.status_code != 200:
        raise HTTPException(status_code=422, detail=f"Episode URL returned status {resp.status_code}")

    soup = BeautifulSoup(resp.text, "html.parser")

    air_date, episode_number = _extract_episode_meta(soup)

    items = _extract_category_clues(soup, category)
    if len(items) != 5:
        raise HTTPException(status_code=422, detail="Could not extract exactly five items for the category with both clue_raw and answer_exact")

    return {
        "episode_air_date": air_date or "",
        "episode_number": episode_number or "",
        "category": category,
        "items": items,
        "source": "J! Archive",
        "episode_url": episode_url,
        "notes": "",
    }


