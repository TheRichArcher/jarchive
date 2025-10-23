J! Archive Verifier API

FastAPI microservice to extract exactly five verified items (clue + correct response) for a given category from a J! Archive episode page.

Endpoint
GET /ja/extract

Query:
episode_url: full J! Archive episode URL (e.g., https://www.j-archive.com/showgame.php?game_id=XXXX)
category: exact category title as shown on the board

Returns:
{
  "episode_air_date": "YYYY-MM-DD",
  "episode_number": "####",
  "category": "INTERNET CELEBRITIES",
  "items": [
    {"value":"$200","clue_raw":"...","answer_exact":"..."},
    {"value":"$400","clue_raw":"...","answer_exact":"..."},
    {"value":"$600","clue_raw":"...","answer_exact":"..."},
    {"value":"$800","clue_raw":"...","answer_exact":"..."},
    {"value":"$1000","clue_raw":"...","answer_exact":"..."}
  ],
  "source": "J! Archive",
  "episode_url": "https://www.j-archive.com/showgame.php?game_id=XXXX",
  "notes": ""
}

If five verified items cannot be extracted, returns 422.



