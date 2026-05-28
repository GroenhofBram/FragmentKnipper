import json
import re
from urllib.parse import urlparse, parse_qs

import streamlit as st


# ---------- Time parsing utilities ----------
def parse_timepart(t: str) -> float:
    t = t.strip()
    if not t:
        raise ValueError("Empty time string")
    if ':' in t:
        parts = [p for p in t.split(':') if p != '']
        parts_f = []
        for p in parts:
            if '.' in p:
                parts_f.append(float(p))
            else:
                parts_f.append(int(p))
        if len(parts_f) == 3:
            h, m, s = parts_f
        elif len(parts_f) == 2:
            h = 0
            m, s = parts_f
        elif len(parts_f) == 1:
            h = 0
            m = 0
            s = parts_f[0]
        else:
            raise ValueError("Bad time format: " + t)
        return float(h) * 3600 + float(m) * 60 + float(s)
    if '.' in t:
        parts = t.split('.')
        if len(parts) == 2:
            m = int(parts[0]) if parts[0] != '' else 0
            s = float(parts[1])
            return float(m) * 60 + float(s)
        else:
            return float(t)
    return float(t)


def parse_ranges(range_string: str):
    if not range_string:
        return []
    raw_pieces = []
    for line in range_string.replace('\n', ',').split(','):
        piece = line.strip()
        if piece:
            raw_pieces.append(piece)
    ranges = []
    for piece in raw_pieces:
        if '-' not in piece:
            raise ValueError("Each range must use '-' to separate start and end: " + piece)
        a, b = piece.split('-', 1)
        start = parse_timepart(a)
        end = parse_timepart(b)
        if end <= start:
            raise ValueError(f"End time must be greater than start time in range '{piece}'")
        ranges.append((start, end))
    return ranges


# ---------- YouTube ID extraction ----------
def extract_yt_id(url: str):
    if not url:
        return None
    url = url.strip()
    # Try standard parse
    try:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower()
        if hostname in ("youtu.be", "www.youtu.be"):
            return parsed.path.lstrip('/')
        if "youtube" in hostname:
            qs = parse_qs(parsed.query)
            if "v" in qs:
                return qs["v"][0]
            # paths like /embed/VIDEOID or /v/VIDEOID
            path_parts = parsed.path.split('/')
            for i, p in enumerate(path_parts):
                if p in ("embed", "v"):
                    if len(path_parts) > i + 1:
                        return path_parts[i + 1]
            # maybe last part is id
            last = path_parts[-1]
            if len(last) == 11:
                return last
    except Exception:
        pass
    # fallback: regex search for 11-char id
    m = re.search(r"([0-9A-Za-z_-]{11})", url)
    if m:
        return m.group(1)
    return None


# ---------- Streamlit UI ----------
st.set_page_config(page_title="YouTube Segment Player (no download)", layout="centered")
st.title("YouTube Segment Player — Play only specified parts (no download)")

st.markdown(
    "Paste a YouTube link and time ranges (comma or newline separated). "
    "Examples: `0.02-0.05`, `1:03-1:20`, `12-15`, `90-95.5`.\n\n"
    "This app embeds the YouTube player and plays only the segments you specify in sequence using the YouTube IFrame API. "
    "No video files are downloaded or processed on the server."
)

url = st.text_input("YouTube URL")
ranges_input = st.text_area("Time ranges (comma or newline separated)", value="0.02-0.05,1.03-1.20", height=100)
autoplay = st.checkbox("Attempt autoplay (may be blocked by browser)", value=False)
loop = st.checkbox("Loop segments (after last segment, start again)", value=False)
open_player = st.button("Open Player")

if open_player:
    if not url.strip():
        st.error("Please enter a YouTube URL.")
    else:
        video_id = extract_yt_id(url)
        if not video_id:
            st.error("Could not extract a YouTube video ID from that URL. Please check the URL.")
        else:
            try:
                ranges = parse_ranges(ranges_input)
                if not ranges:
                    st.error("No valid ranges parsed. Enter at least one range.")
                else:
                    # Build the HTML + JS that uses YouTube IFrame API
                    segments_json = json.dumps([[float(s), float(e)] for (s, e) in ranges])
                    autoplay_flag = "true" if autoplay else "false"
                    loop_flag = "true" if loop else "false"
                    # Sizing: responsive width
                    html = f"""
<!doctype html>
<html>
  <head>
    <style>
      body {{ font-family: Arial, sans-serif; margin: 0; padding: 8px; }}
      #controls {{ margin-top: 8px; }}
      button {{ margin-right: 6px; }}
      #segments {{ margin-top: 8px; font-size: 14px; }}
    </style>
  </head>
  <body>
    <div id="player"></div>
    <div id="controls">
      <button id="playAll">Play segments</button>
      <button id="pause">Pause</button>
      <button id="next">Next</button>
      <button id="prev">Prev</button>
      <label><input type="checkbox" id="loop" {"checked" if loop else ""}> Loop</label>
    </div>
    <div id="segments"></div>

    <script>
      var videoId = "{video_id}";
      var segments = {segments_json};
      var currentIndex = 0;
      var checkInterval = null;
      var player = null;
      var userLoop = {loop_flag};
      var autoplay = {autoplay_flag};

      // Render list
      function renderSegments() {{
        var el = document.getElementById('segments');
        var html = '<b>Segments:</b><ol>';
        for (var i=0;i<segments.length;i++) {{
          html += '<li>' + secondsToString(segments[i][0]) + ' → ' + secondsToString(segments[i][1]) + (i===currentIndex ? ' <strong>(current)</strong>' : '') + '</li>';
        }}
        html += '</ol>';
        el.innerHTML = html;
      }}

      function secondsToString(s) {{
        s = Math.floor(s);
        var h = Math.floor(s / 3600);
        var m = Math.floor((s % 3600) / 60);
        var sec = s % 60;
        if (h>0) return h + ':' + String(m).padStart(2,'0') + ':' + String(sec).padStart(2,'0');
        return m + ':' + String(sec).padStart(2,'0');
      }}

      // Load YouTube IFrame API
      var tag = document.createElement('script');
      tag.src = "https://www.youtube.com/iframe_api";
      document.head.appendChild(tag);

      function onYouTubeIframeAPIReady() {{
        player = new YT.Player('player', {{
          height: '390',
          width: '640',
          videoId: videoId,
          playerVars: {{
            'rel': 0,
            'modestbranding': 1,
            'controls': 1,
            'autoplay': autoplay ? 1 : 0
          }},
          events: {{
            'onReady': onPlayerReady,
            'onStateChange': onPlayerStateChange
          }}
        }});
      }}

      function onPlayerReady(event) {{
        renderSegments();
      }}

      function onPlayerStateChange(event) {{
        // no-op; we rely on our timer to progress segments
      }}

      function playSegment(idx) {{
        if (!player) return;
        if (idx < 0) idx = 0;
        if (idx >= segments.length) {{
          if (userLoop) {{
            idx = 0;
          }} else {{
            player.pauseVideo();
            return;
          }}
        }}
        currentIndex = idx;
        var start = Number(segments[currentIndex][0]);
        var end = Number(segments[currentIndex][1]);
        try {{ player.seekTo(start, true); }} catch(e) {{ console.warn(e); }}
        // If autoplay is blocked, user must press play; attempt to play anyway
        try {{ player.playVideo(); }} catch(e) {{ console.warn(e); }}
        if (checkInterval) clearInterval(checkInterval);
        checkInterval = setInterval(function() {{
          if (!player || typeof player.getCurrentTime !== 'function') return;
          var now = player.getCurrentTime();
          // small tolerance to account for seek inaccuracy and buffering
          if (now >= end - 0.15) {{
            clearInterval(checkInterval);
            // Advance to next segment
            currentIndex += 1;
            if (currentIndex < segments.length) {{
              playSegment(currentIndex);
            }} else {{
              if (userLoop) {{
                playSegment(0);
              }} else {{
                player.pauseVideo();
              }}
            }}
            renderSegments();
          }}
        }}, 200);
        renderSegments();
      }}

      document.getElementById('playAll').addEventListener('click', function() {{
        userLoop = document.getElementById('loop').checked;
        playSegment(0);
      }});
      document.getElementById('pause').addEventListener('click', function() {{
        if (player) player.pauseVideo();
        if (checkInterval) clearInterval(checkInterval);
      }});
      document.getElementById('next').addEventListener('click', function() {{
        if (checkInterval) clearInterval(checkInterval);
        playSegment(currentIndex + 1);
      }});
      document.getElementById('prev').addEventListener('click', function() {{
        if (checkInterval) clearInterval(checkInterval);
        playSegment(Math.max(0, currentIndex - 1));
      }});

      // expose helpful keyboard shortcuts
      document.addEventListener('keydown', function(e) {{
        if (e.key === ' ') {{
          if (player) {{
            var state = player.getPlayerState();
            if (state === YT.PlayerState.PLAYING) {{
              player.pauseVideo();
              if (checkInterval) clearInterval(checkInterval);
            }} else {{
              player.playVideo();
            }}
          }}
          e.preventDefault();
        }} else if (e.key === 'n') {{
          document.getElementById('next').click();
        }} else if (e.key === 'p') {{
          document.getElementById('prev').click();
        }}
      }});
    </script>
  </body>
</html>
"""
                    # Render HTML in Streamlit
                    st.components.v1.html(html, height=560, scrolling=True)
            except Exception as e:
                st.error(f"Could not parse ranges: {e}")
