import json
import re
from urllib.parse import urlparse, parse_qs
import streamlit as st

# ---------- Time parsing utilities ----------
def parse_timepart(t: str) -> float:
    """
    Parse a time string into seconds.
    Supported formats:
    - HH:MM:SS(.ms), MM:SS(.ms), SS(.ms)
    - "m.s" is interpreted heuristically:
        * If integer part >= 1 and fractional part looks like seconds (0-59), it's treated as M.S (minutes.seconds).
    """
    if t is None:
        raise ValueError("Empty time string")
    t = t.strip()
    if not t:
        raise ValueError("Empty time string")

    # HH:MM:SS or MM:SS or SS (with optional decimal part on seconds)
    if ':' in t:
        parts = [p for p in t.split(':') if p != '']
        if len(parts) > 3:
            raise ValueError(f"Bad time format: {t}")
        parts_f = []
        for i, p in enumerate(parts):
            if i == len(parts) - 1:
                parts_f.append(float(p))
            else:
                parts_f.append(int(p))
        if len(parts_f) == 3:
            h, m, s = parts_f
        elif len(parts_f) == 2:
            h = 0
            m, s = parts_f
        else:  # 1 part
            h = 0
            m = 0
            s = parts_f[0]
        return float(h) * 3600.0 + float(m) * 60.0 + float(s)

    # No colon, but contains a dot
    if '.' in t:
        left, right = t.split('.', 1)
        if left == '':
            # ".5" => 0.5 seconds
            return float(t)
        try:
            li = int(left)
            # If integer part >= 1 and the fractional part looks like seconds (<60 and up to two digits),
            # treat it as minutes.seconds.
            if li >= 1 and re.fullmatch(r'\d{1,2}$', right):
                ri = int(right)
                if 0 <= ri < 60:
                    return float(li) * 60.0 + float(ri)
            # Otherwise, fall back to decimal seconds
            return float(t)
        except ValueError:
            return float(t)

    # Plain integer seconds
    return float(t)


# ---------- Range parsing (with optional per-range padding) ----------
def parse_ranges_with_padding(range_string: str, default_pad: float):
    """
    Parse a string containing comma/newline separated time ranges.
    Accepts optional per-range padding annotations like:
      - "0.00-4.43|0.2"
      - "0.00-4.43 pad:0.2"
      - "0.00-4.43 p=0.2"
    If no padding specified, default_pad is used.
    Ignores parenthetical annotations like "(knip)".
    Returns a list of (start_seconds, end_seconds, padding_seconds).
    """
    if not range_string:
        return []
    s = re.sub(r'\([^)]*\)', '', range_string)  # remove parenthetical annotations
    pieces = [p.strip() for p in re.split(r'[,\n]+', s) if p.strip()]
    ranges = []
    rng_re = re.compile(r'(\d+(?::\d+){0,2}(?:\.\d+)?)[\s\-–—]+(\d+(?::\d+){0,2}(?:\.\d+)?)')
    pad_re_list = [
        re.compile(r'\|\s*([0-9]+(?:\.\d+)?)'),                 # |0.2
        re.compile(r'pad[:=]?\s*([0-9]+(?:\.\d+)?)', re.I),     # pad:0.2 or pad=0.2 or pad 0.2
        re.compile(r'\bp[:=]?\s*([0-9]+(?:\.\d+)?)\b', re.I),   # p:0.2 or p=0.2
    ]
    for piece in pieces:
        m = rng_re.search(piece)
        if not m:
            continue
        a, b = m.group(1), m.group(2)
        try:
            start = parse_timepart(a.strip())
            end = parse_timepart(b.strip())
        except Exception:
            continue
        if end <= start:
            continue
        pad = None
        tail = piece[m.end():]
        for pre in pad_re_list:
            pm = pre.search(tail)
            if pm:
                try:
                    pad = float(pm.group(1))
                except Exception:
                    pad = None
                break
        if pad is None:
            pad = float(default_pad)
        ranges.append((start, end, float(pad)))
    return ranges


# ---------- Simple ranges parser for cuts (no padding) ----------
def parse_ranges_simple(range_string: str):
    """
    Parse ranges for cuts. Ignore parenthetical annotations.
    Returns list of (start_seconds, end_seconds).
    """
    if not range_string:
        return []
    s = re.sub(r'\([^)]*\)', '', range_string)
    pieces = [p.strip() for p in re.split(r'[,\n]+', s) if p.strip()]
    ranges = []
    rng_re = re.compile(r'(\d+(?::\d+){0,2}(?:\.\d+)?)[\s\-–—]+(\d+(?::\d+){0,2}(?:\.\d+)?)')
    for piece in pieces:
        m = rng_re.search(piece)
        if not m:
            continue
        a, b = m.group(1), m.group(2)
        try:
            start = parse_timepart(a.strip())
            end = parse_timepart(b.strip())
        except Exception:
            continue
        if end <= start:
            continue
        ranges.append((start, end))
    return ranges


# ---------- Interval utilities ----------
def merge_intervals_with_pad(intervals_with_pad, eps=1e-6):
    """
    Merge overlapping/adjacent intervals while preserving/combining padding.
    When merging multiple intervals the resulting padding is set to the max padding among them.
    """
    if not intervals_with_pad:
        return []
    intervals_with_pad = sorted(intervals_with_pad, key=lambda x: x[0])
    merged = []
    cur_s, cur_e, cur_p = intervals_with_pad[0]
    for s, e, p in intervals_with_pad[1:]:
        if s <= cur_e + eps:
            cur_e = max(cur_e, e)
            cur_p = max(cur_p, p)
        else:
            merged.append((cur_s, cur_e, cur_p))
            cur_s, cur_e, cur_p = s, e, p
    merged.append((cur_s, cur_e, cur_p))
    return merged


def merge_intervals(intervals, eps=1e-6):
    """Sort and merge overlapping/adjacent intervals. intervals is list of (s,e)."""
    if not intervals:
        return []
    intervals = sorted(intervals, key=lambda x: x[0])
    merged = []
    cur_s, cur_e = intervals[0]
    for s, e in intervals[1:]:
        if s <= cur_e + eps:
            cur_e = max(cur_e, e)
        else:
            merged.append((cur_s, cur_e))
            cur_s, cur_e = s, e
    merged.append((cur_s, cur_e))
    return merged


def subtract_cuts_from_padded_segments(segments_with_pad, cuts):
    """
    segments_with_pad: list of (s,e,p)
    cuts: list of (cs,ce)
    Returns list of (s,e,p) where cuts have been removed and each resulting piece inherits the original p.
    """
    if not segments_with_pad:
        return []
    if not cuts:
        return list(segments_with_pad)

    cuts = merge_intervals(cuts)
    result = []
    for s, e, p in segments_with_pad:
        cur = s
        for cs, ce in cuts:
            if ce <= cur:
                continue
            if cs >= e:
                break
            # overlap exists
            if cs <= cur and ce >= e:
                cur = e
                break
            if cs <= cur < ce < e:
                cur = ce
                continue
            if cur < cs < e <= ce:
                if cs - cur > 1e-9:
                    result.append((cur, cs, p))
                cur = e
                break
            if cur < cs and ce < e:
                if cs - cur > 1e-9:
                    result.append((cur, cs, p))
                cur = ce
                continue
        if cur < e - 1e-9:
            result.append((cur, e, p))

    # Merge adjacent pieces only when padding matches exactly
    if not result:
        return []
    merged = []
    cur_s, cur_e, cur_p = result[0]
    for s, e, p in result[1:]:
        if s <= cur_e + 1e-6 and abs(p - cur_p) < 1e-9:
            cur_e = max(cur_e, e)
        else:
            merged.append((cur_s, cur_e, cur_p))
            cur_s, cur_e, cur_p = s, e, p
    merged.append((cur_s, cur_e, cur_p))
    return merged


# ---------- YouTube ID extraction ----------
def extract_yt_id(url: str):
    if not url:
        return None
    url = url.strip()
    try:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower()
        if hostname in ("youtu.be", "www.youtu.be"):
            return parsed.path.lstrip('/')
        if "youtube" in hostname:
            qs = parse_qs(parsed.query)
            if "v" in qs:
                return qs["v"][0]
            path_parts = [p for p in parsed.path.split('/') if p != '']
            for i, p in enumerate(path_parts):
                if p in ("embed", "v"):
                    if len(path_parts) > i + 1:
                        return path_parts[i + 1]
            last = path_parts[-1] if path_parts else ''
            if len(last) == 11:
                return last
    except Exception:
        pass
    m = re.search(r"([0-9A-Za-z_-]{11})", url)
    if m:
        return m.group(1)
    return None


# ---------- Streamlit UI ----------
st.set_page_config(page_title="YouTube Segment Player", layout="centered")
st.markdown("<h1 style='margin-bottom:0.2rem'>YouTube Segment Player — Play only specified parts (no download)</h1>", unsafe_allow_html=True)
st.markdown(
    """
Paste a YouTube link and time ranges (comma or newline separated). Examples of supported time formats:
- 0.02-0.05  (decimal seconds)
- 1:03-1:20  (MM:SS)
- 12-15
- 90-95.5

You can also provide "Sections to cut" — time ranges that will be removed from the playback.
Extra text like "(knip)" or other annotations will be ignored automatically.

Per-section padding:
- You can specify a per-range padding with `|<seconds>` or `pad:<seconds>`.
- If not provided the base padding used is 1.0 second.
- After the player loads, you can edit each segment's padding inline in the segments box; changes apply immediately.
""".strip()
)

# Layout: left for inputs, right for options
col1, col2 = st.columns([2, 1])
with col1:
    url = st.text_input("YouTube URL", placeholder="https://www.youtube.com/watch?v=VIDEO_ID")
    ranges_input = st.text_area(
        "Time ranges to play (comma or newline separated). You may add per-range padding with '|0.2' or 'pad:0.2'.",
        value="0.00-4.43",
        height=160,
        help="Examples: 0.02-0.05|0.2, 1:03-1:20 pad:0.5, 12-15"
    )
    cuts_input = st.text_area(
        "Sections to cut (these will be removed from the playback)",
        value="3.12-3.32 (knip), 4.44-5.21 (knip)",
        height=120,
        help="Enter ranges that should be removed from the playback. Annotations like '(knip)' are ignored."
    )
    example_expander = st.expander("Show example inputs")
    with example_expander:
        st.markdown(
            "- Play ranges: `0.00-4.43|0.1` (plays 0.00→4.43 with 0.1s padding at the end)\n"
            "- Cuts: `3.12-3.32 (knip), 4.44-5.21 (knip)`\n"
            "- If you omit per-range padding, a base padding of 1.0s is used."
        )

with col2:
    st.markdown("Options")
    autoplay = st.checkbox("Attempt autoplay (may be blocked by browser)", value=True)
    loop = st.checkbox("Loop segments", value=True)
    # No global padding setting; base padding fixed to 1.0 (used when ranges don't specify a pad)
    merge_adjacent = True  # always on

st.write("")  # small spacer
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
                base_pad = 1.0
                parsed_ranges = parse_ranges_with_padding(ranges_input, base_pad)
                parsed_cuts = parse_ranges_simple(cuts_input)

                if not parsed_ranges:
                    st.error("No valid ranges parsed. Enter at least one range to play.")
                else:
                    if merge_adjacent:
                        parsed_ranges = merge_intervals_with_pad(parsed_ranges)
                        parsed_cuts = merge_intervals(parsed_cuts)

                    final_segments = subtract_cuts_from_padded_segments(parsed_ranges, parsed_cuts)

                    if not final_segments:
                        st.error("No segments remain after applying cuts.")
                    else:
                        # Build HTML/JS player. The segments array includes per-segment padding as third value.
                        segments_json = json.dumps([[float(s), float(e), float(p)] for (s, e, p) in final_segments])
                        autoplay_flag = "true" if autoplay else "false"
                        loop_flag = "true" if loop else "false"

                        html = f"""
<!doctype html>
<html>
  <head>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
      :root {{
        --bg: #ffffff;
        --card: #fbfbfb;
        --muted: #6b7280;
        --accent: #1a73e8;
        --radius: 10px;
        --shadow: 0 6px 20px rgba(18, 18, 18, 0.06);
      }}
      body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial; margin: 12px; background: var(--bg); color: #111827; }}
      #container {{ max-width: 980px; margin: 0 auto; }}
      .card {{ background: var(--card); border-radius: var(--radius); padding: 12px; box-shadow: var(--shadow); }}
      #player {{ width: 100%; aspect-ratio: 16/9; border-radius: 8px; overflow: hidden; background: #000; }}
      #controls {{ margin-top: 12px; display:flex; flex-wrap:wrap; gap:8px; align-items:center; }}
      button {{
        background: linear-gradient(180deg,#ffffff,#f4f4f4);
        border: 1px solid #e6e7ea;
        padding: 8px 14px;
        border-radius: 8px;
        cursor: pointer;
        font-size: 14px;
      }}
      button.primary {{
        background: linear-gradient(180deg,var(--accent), #1666c3);
        color: white;
        border: none;
      }}
      .pad-input {{
        width:72px;
        padding:4px 6px;
        border-radius:6px;
        border:1px solid #ddd;
        font-size:13px;
        margin-left:8px;
      }}
      .seg-row {{ display:flex; align-items:center; gap:8px; margin-bottom:6px; }}
      .seg-times {{ font-family: monospace; }}
      .muted {{ color: var(--muted); font-size:13px; margin-left:auto; }}
      .hint {{ color: var(--muted); font-size:13px; margin-top:6px; }}
      .current {{ color: var(--accent); font-weight:700; margin-left:6px; }}
    </style>
  </head>
  <body>
    <div id="container" class="card">
      <div id="player"></div>
      <div id="controls">
        <button id="playAll" class="primary">Play segments</button>
        <button id="pause">Pause</button>
        <button id="prev">Prev</button>
        <button id="next">Next</button>
        <label style="margin-left:8px;">
          <input type="checkbox" id="loop" {"checked" if loop else ""}> Loop
        </label>
        <div class="muted">Autoplay attempt: {autoplay}</div>
      </div>

      <div id="segments" class="card" style="margin-top:12px;"></div>
      <div class="hint">Click a pad box and change the value to adjust padding for that segment. Changes take effect immediately.</div>
    </div>

    <script>
      var videoId = "{video_id}";
      // Each segment: [start, end, pad]
      var segments = {segments_json};
      var currentIndex = 0;
      var checkInterval = null;
      var player = null;
      var userLoop = {loop_flag};
      var autoplay = {autoplay_flag};

      function secondsToString(s) {{
        var total = Number(s);
        if (!isFinite(total)) return String(s);
        var h = Math.floor(total / 3600);
        var m = Math.floor((total % 3600) / 60);
        var sec = total % 60;
        var secInt = Math.floor(sec);
        var frac = sec - secInt;
        var fracStr = (frac > 0) ? frac.toFixed(2).substring(1) : '.00';
        var secStr = String(secInt).padStart(2, '0') + fracStr;
        if (h > 0) {{
          return h + ':' + String(m).padStart(2, '0') + ':' + secStr;
        }}
        return String(m) + ':' + secStr;
      }}

      function renderSegments() {{
        var el = document.getElementById('segments');
        var html = '<b>Segments:</b><div style="margin-top:8px">';
        for (var i=0;i<segments.length;i++) {{
          var s = secondsToString(segments[i][0]);
          var e = secondsToString(segments[i][1]);
          var p = (segments[i].length > 2) ? Number(segments[i][2]).toFixed(3) : '1.000';
          var currentMark = (i === currentIndex) ? '<span class="current">(current)</span>' : '';
          html += '<div class="seg-row">';
          html += '<div class="seg-times"><strong>' + (i+1) + '.</strong>&nbsp;&nbsp;' + s + ' → ' + e + '</div>';
          html += '<div style="margin-left:6px;color:#6b7280;">(pad: </div>';
          html += '<input type="number" min="0" max="5" step="0.01" class="pad-input" id="pad_' + i + '" value="' + p + '" oninput="onPadInput(' + i + ', this.value)">';
          html += '<div style="color:#6b7280;">s)</div>';
          html += currentMark;
          html += '</div>';
        }}
        html += '</div>';
        el.innerHTML = html;
      }}

      function onPadInput(idx, val) {{
        var v = parseFloat(val);
        if (!isFinite(v) || v < 0) v = 0;
        if (v > 5) v = 5;
        // store the new pad value immediately
        if (!Array.isArray(segments[idx])) return;
        segments[idx][2] = v;
        // update the input value to the sanitized value (preserve focus)
        var inp = document.getElementById('pad_' + idx);
        if (inp) inp.value = v.toFixed(3);
        // If the changed segment is currently playing, we don't restart it;
        // the running interval recalculates the effective end using the latest pad value,
        // so changes take effect immediately (including immediate advancement if new pad makes effectiveEnd <= now).
      }

      // Load YouTube IFrame API
      (function() {{
        var tag = document.createElement('script');
        tag.src = "https://www.youtube.com/iframe_api";
        document.head.appendChild(tag);
      }})();

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
        if (autoplay) {{
          setTimeout(function() {{
            userLoop = document.getElementById('loop').checked;
            playSegment(0);
          }}, 250);
        }}
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
            try {{ player.pauseVideo(); }} catch(e) {{}}
            return;
          }}
        }}
        currentIndex = idx;
        var start = Number(segments[currentIndex][0]);
        var end = Number(segments[currentIndex][1]);
        var segLength = Math.max(0.0, end - start);

        // Clear any previous interval before seeking/playing
        if (checkInterval) {{
          clearInterval(checkInterval);
          checkInterval = null;
        }}

        // Seek and play with a short delay to improve reliability for tiny segments.
        setTimeout(function() {{
          try {{ player.seekTo(start, true); }} catch(e) {{ console.warn(e); }}
          try {{ player.playVideo(); }} catch(e) {{ console.warn(e); }}
          setTimeout(function() {{
            try {{ player.playVideo(); }} catch(e) {{ /* ignore */ }}
          }}, 120);
        }}, 40);

        // Tolerance: will be recalculated each tick based on current pad value.
        var checkFreq = segLength < 0.25 ? 30 : 100;
        checkInterval = setInterval(function() {{
          if (!player || typeof player.getCurrentTime !== 'function') return;
          var now = player.getCurrentTime();

          // get the latest pad for the currentIndex (may have been changed by user)
          var rawPad = (segments[currentIndex] && segments[currentIndex].length > 2) ? Number(segments[currentIndex][2]) : 1.0;
          if (!isFinite(rawPad) || rawPad < 0) rawPad = 0;
          // clamp pad relative to segment length like the server-side logic:
          var padding = rawPad;
          if (segLength > 0) {{
            padding = Math.min(padding, Math.max(0.001, segLength / 4));
          }} else {{
            padding = Math.max(0.001, padding);
          }}
          var effectiveEnd = end + padding;
          var tolerance = Math.max(0.005, Math.min(0.05, padding * 0.5));

          // update the pad input to reflect clamped value (so UI shows what's actually used)
          var padInput = document.getElementById('pad_' + currentIndex);
          if (padInput) padInput.value = padding.toFixed(3);

          // Only consider advancing after we've definitely started the segment.
          if (now >= start - 0.02 && now >= (effectiveEnd - tolerance)) {{
            clearInterval(checkInterval);
            checkInterval = null;
            currentIndex += 1;
            if (currentIndex < segments.length) {{
              playSegment(currentIndex);
            }} else {{
              if (userLoop) {{
                playSegment(0);
              }} else {{
                try {{ player.pauseVideo(); }} catch(e) {{ /* ignore */ }}
              }}
            }}
            renderSegments();
          }}
        }}, checkFreq);
        renderSegments();
      }}

      document.getElementById('playAll').addEventListener('click', function() {{
        userLoop = document.getElementById('loop').checked;
        playSegment(0);
      }});
      document.getElementById('pause').addEventListener('click', function() {{
        if (player) try {{ player.pauseVideo(); }} catch(e) {{ /* ignore */ }}
        if (checkInterval) clearInterval(checkInterval);
        checkInterval = null;
      }});
      document.getElementById('next').addEventListener('click', function() {{
        if (checkInterval) clearInterval(checkInterval);
        checkInterval = null;
        playSegment(currentIndex + 1);
      }});
      document.getElementById('prev').addEventListener('click', function() {{
        if (checkInterval) clearInterval(checkInterval);
        checkInterval = null;
        playSegment(Math.max(0, currentIndex - 1));
      }});
      // Keyboard shortcuts
      document.addEventListener('keydown', function(e) {{
        if (e.key === ' ') {{
          if (player) {{
            var state = player.getPlayerState();
            if (state === YT.PlayerState.PLAYING) {{
              try {{ player.pauseVideo(); }} catch(e) {{ /* ignore */ }}
              if (checkInterval) clearInterval(checkInterval);
              checkInterval = null;
            }} else {{
              try {{ player.playVideo(); }} catch(e) {{ /* ignore */ }}
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
                        st.components.v1.html(html, height=720, scrolling=True)
            except Exception as e:
                st.error(f"Could not parse ranges or apply cuts: {e}")
