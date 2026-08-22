import os
import threading
import uuid
from urllib.parse import urlparse, parse_qs

from flask import Flask, request, jsonify, send_file
import yt_dlp
from yt_dlp.utils import download_range_func

app = Flask(__name__)
DOWNLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'downloads')
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

progress = {}

def parse_time(t):
    t = t.strip()
    if ':' in t:
        parts = t.split(':')
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        elif len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    else:
        return float(t)
    raise ValueError(f"Invalid time format: {t}")

def download_task(job_id, url, start_sec, end_sec, resolution, output_path):
    try:
        progress[job_id]['percent'] = 10

        # Build a format selector that picks the best video with height <= resolution,
        # and then the best audio.
        # The '+bestaudio/best' merges the best audio format.
        # This ensures we get the highest quality within the limit.
        format_string = f'bestvideo[height<={resolution}]+bestaudio/best'

        # We'll also use format_sort to prefer your exact resolution if available,
        # but the filter above already does the job.
        sort_string = [f'res:{resolution}', '+size']

        def hook(d):
            if d['status'] == 'downloading':
                total = d.get('total_bytes') or d.get('total_bytes_estimate', 1)
                downloaded = d.get('downloaded_bytes', 0)
                percent = (downloaded / total) * 100 if total > 0 else 0
                scaled = 10 + (percent * 0.8)
                progress[job_id]['percent'] = round(min(scaled, 90), 1)
            elif d['status'] == 'finished':
                progress[job_id]['percent'] = 95

        ydl_opts = {
            'format': format_string,          # 👈 Primary selector
            'format_sort': sort_string,       # 👈 Secondary refinement
            'merge_output_format': 'mp4',
            'outtmpl': output_path,
            'verbose': True,                  # Set to True to see selection in terminal
            'quiet': False,
            'no_warnings': False,
            'cookiesfrombrowser': ('chrome',),
            # 'cookiefile': 'cookies.txt',    # Uncomment if browser method fails
            'download_ranges': download_range_func(None, [(start_sec, end_sec)]),
            'force_keyframes_at_cuts': True,
            # Rate limit protection
            'sleep_interval': 10,
            'max_sleep_interval': 20,
            'sleep_requests': 2,
            'retries': 5,
            'fragment_retries': 5,
            'extractor_args': {
                'youtube': {
                    'player_client': ['web', 'android', 'ios'],
                    'skip': ['hls', 'dash'],
                }
            },
            'progress_hooks': [hook],
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # Extract info to see what format was selected (for debugging)
            info = ydl.extract_info(url, download=False)
            # Log the selected format
            selected_format = info.get('requested_formats', [info.get('format')])[0]
            print(f"Selected format: {selected_format.get('format_id')} - {selected_format.get('height')}p")
            # Now download
            ydl.download([url])

        if not os.path.exists(output_path):
            raise Exception('File was not created')

        progress[job_id]['percent'] = 100
        progress[job_id]['filename'] = os.path.basename(output_path)

    except Exception as e:
        error_msg = str(e)
        progress[job_id]['error'] = error_msg
        progress[job_id]['percent'] = -1
        # Do not delete the file – keep for debugging
        # if os.path.exists(output_path):
        #     os.remove(output_path)
        print(f"ERROR: {error_msg}")

@app.route('/download', methods=['GET'])
def download_clip():
    url = request.args.get('url')
    start_str = request.args.get('start')
    end_str = request.args.get('end')
    resolution = request.args.get('resolution', '480')

    if not url or not start_str or not end_str:
        return jsonify({'status': 'error', 'message': 'Missing parameters'}), 400

    try:
        start_sec = parse_time(start_str)
        end_sec = parse_time(end_str)
        if start_sec >= end_sec:
            return jsonify({'status': 'error', 'message': 'End time must be after start'}), 400
    except ValueError as e:
        return jsonify({'status': 'error', 'message': str(e)}), 400

    parsed = urlparse(url)
    if 'youtu.be' in parsed.netloc:
        video_id = parsed.path.lstrip('/')
    else:
        qs = parse_qs(parsed.query)
        video_id = qs.get('v', [None])[0]
    if not video_id:
        return jsonify({'status': 'error', 'message': 'Could not extract video ID'}), 400

    safe_start = start_str.replace(':', '-')
    safe_end = end_str.replace(':', '-')
    output_filename = f"{video_id}_clip_{resolution}p_{safe_start}_{safe_end}.mp4"
    output_path = os.path.join(DOWNLOAD_FOLDER, output_filename)

    if os.path.exists(output_path):
        os.remove(output_path)

    job_id = str(uuid.uuid4())
    progress[job_id] = {'percent': 0, 'filename': None, 'error': None}

    thread = threading.Thread(
        target=download_task,
        args=(job_id, url, start_sec, end_sec, resolution, output_path)
    )
    thread.daemon = True
    thread.start()

    return jsonify({
        'status': 'accepted',
        'job_id': job_id
    })

@app.route('/progress/<job_id>', methods=['GET'])
def get_progress(job_id):
    if job_id not in progress:
        return jsonify({'status': 'error', 'message': 'Job not found'}), 404

    data = progress[job_id]

    if data['error']:
        return jsonify({'status': 'error', 'message': data['error']}), 500

    if data['percent'] >= 100:
        return jsonify({
            'status': 'complete',
            'filename': data['filename']
        })

    return jsonify({
        'status': 'processing',
        'percent': data['percent']
    })

@app.route('/file/<filename>', methods=['GET'])
def serve_file(filename):
    if '..' in filename or '/' in filename or '\\' in filename:
        return jsonify({'status': 'error', 'message': 'Invalid filename'}), 400
    file_path = os.path.join(DOWNLOAD_FOLDER, filename)
    if not os.path.exists(file_path):
        return jsonify({'status': 'error', 'message': 'File not found'}), 404
    return send_file(file_path, as_attachment=True, download_name=filename)

if __name__ == '__main__':
    app.run(host='localhost', port=5000, debug=False)