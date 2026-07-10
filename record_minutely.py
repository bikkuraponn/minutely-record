import os
import sys
import time
from pathlib import Path
from datetime import datetime, timedelta, timezone
import dotenv
from zoneinfo import ZoneInfo
from googleapiclient.errors import HttpError
import video_data
import youtube_channel_stats
from turso_client import TursoClient

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS stats_minutely (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    comment_count INTEGER,
    view_count INTEGER,
    like_count INTEGER,
    channel_comment_count INTEGER,
    channel_view_count INTEGER,
    channel_like_count INTEGER,
    lailala_topic_view_count INTEGER,
    lailala_topic_like_count INTEGER,
    lailala_topic_comment_count INTEGER
)
"""

_CREATE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_stats_minutely_created ON stats_minutely(created_at)
"""

_INSERT = """
INSERT INTO stats_minutely (
    created_at, comment_count, view_count, like_count,
    channel_comment_count, channel_view_count, channel_like_count,
    lailala_topic_view_count, lailala_topic_like_count, lailala_topic_comment_count
) VALUES (?,?,?,?,?,?,?,?,?,?)
"""

def wait_until_next_minute():
    now = datetime.now(timezone.utc)
    next_minute = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
    wait_seconds = (next_minute - now).total_seconds()
    if 0 < wait_seconds:
        time.sleep(wait_seconds)

def parse_timestamp():
    return datetime.now(ZoneInfo("Asia/Tokyo")).isoformat()

dotenv.load_dotenv(Path(__file__).parent.parent / "flaskr" / ".env")
_API_KEYS = [k for k in [
    os.getenv("YOUTUBE_API_KEY"),
    os.getenv("YOUTUBE_API_KEY2"),
] if k]
VIDEO_ID = os.getenv("VIDEO_ID")
LAILALA_CHANNEL_ID = "UCXv9vrrU0MN-VPMRfns0hGA"

_key_idx = 0


def _is_quota_error(e: HttpError) -> bool:
    err = str(e).lower()
    return e.resp.status in (403, 429) and any(s in err for s in [
        "quotaexceeded", "dailylimitexceeded",
        "userdailylimitexceeded", "ratelimitexceeded",
    ])


def fetch_with_rotation(fn, *args):
    """クォータ枯渇時に次のキーへローテーションして再試行する。"""
    global _key_idx
    for _ in range(len(_API_KEYS)):
        try:
            return fn(_API_KEYS[_key_idx], *args)
        except HttpError as e:
            if _is_quota_error(e):
                _key_idx = (_key_idx + 1) % len(_API_KEYS)
                print(f"クォータ枯渇 → キー {_key_idx + 1}/{len(_API_KEYS)} にローテーション")
                continue
            raise
    print("ERROR: 全キーのクォータが枯渇。今回の記録をスキップ")
    sys.exit(0)

turso = TursoClient(os.getenv("TURSO_URL"), os.getenv("TURSO_AUTH_TOKEN"))
turso.execute(_CREATE_TABLE)
turso.execute(_CREATE_INDEX)

wait_until_next_minute()

video_stats = fetch_with_rotation(video_data.get_video_stats, VIDEO_ID)
channel_stats = fetch_with_rotation(youtube_channel_stats.get_channel_statistics, "UC5gv-G5kbtTEoEo1cpmybUQ")
lailala_stats = fetch_with_rotation(youtube_channel_stats.get_channel_statistics, LAILALA_CHANNEL_ID)

turso.execute(_INSERT, [
    parse_timestamp(),
    video_stats["comment_count"],
    video_stats["view_count"],
    video_stats["like_count"],
    channel_stats["total_comments"],
    channel_stats["total_views"],
    channel_stats["total_likes"],
    lailala_stats["total_views"],
    lailala_stats["total_likes"],
    lailala_stats["total_comments"],
])
