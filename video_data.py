from googleapiclient.discovery import build
import dotenv
import os

dotenv.load_dotenv()

def get_video_stats(api_key, video_id):
    youtube = build('youtube', 'v3', developerKey=api_key)
    video_response = youtube.videos().list(
        part='statistics',
        id=video_id
    ).execute()
    stats = video_response['items'][0]['statistics']
    return {
        'comment_count': int(stats.get('commentCount', 0)),
        'view_count': int(stats.get('viewCount', 0)),
        'like_count': int(stats.get('likeCount', 0))
    }
