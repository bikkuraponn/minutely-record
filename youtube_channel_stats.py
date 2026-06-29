from googleapiclient.discovery import build
import dotenv
import os

dotenv.load_dotenv()

def get_channel_statistics(api_key, channel_id):
    youtube = build('youtube', 'v3', developerKey=api_key)

    channel_response = youtube.channels().list(
        part='contentDetails',
        id=channel_id
    ).execute()

    if not channel_response['items']:
        return None

    uploads_playlist_id = channel_response['items'][0]['contentDetails']['relatedPlaylists']['uploads']

    next_page_token = None
    video_ids = []
    while True:
        playlist_response = youtube.playlistItems().list(
            part='contentDetails',
            playlistId=uploads_playlist_id,
            maxResults=50,
            pageToken=next_page_token
        ).execute()
        for item in playlist_response['items']:
            video_ids.append(item['contentDetails']['videoId'])
        next_page_token = playlist_response.get('nextPageToken')
        if not next_page_token:
            break

    total_views = total_likes = total_comments = video_count = 0
    for i in range(0, len(video_ids), 50):
        video_response = youtube.videos().list(
            part='statistics',
            id=','.join(video_ids[i:i+50])
        ).execute()
        for video in video_response['items']:
            stats = video['statistics']
            total_views += int(stats.get('viewCount', 0))
            total_likes += int(stats.get('likeCount', 0))
            total_comments += int(stats.get('commentCount', 0))
            video_count += 1

    return {
        'channel_id': channel_id,
        'video_count': video_count,
        'total_views': total_views,
        'total_likes': total_likes,
        'total_comments': total_comments,
    }
