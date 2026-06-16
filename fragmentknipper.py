import json
import re
from urllib.parse import urlparse, parse_qs
import streamlit as st

# parsen
def parse_timepart(t: str) -> float:
    if t is None:
        raise ValueError("Lege tijdstring")
    t = t.strip()
    if not t:
        raise ValueError("Lege tijdstring")
    if ':' in t:
        parts = [p for p in t.split(':') if p != '']
        if len(parts) > 3:
            raise ValueError(f"Tijdsformaat is fout: {t}")
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
        else:
            h = 0
            m = 0
            s = parts_f[0]
        return float(h) * 3600.0 + float(m) * 60.0 + float(s)
    if '.' in t:
        left, right = t.split('.', 1)
        if left == '':
            return float(t)
        try:
            li = int(left)
            if li >= 1 and re.fullmatch(r'\d{1,2}$', right):
                ri = int(right)
                if 0 <= ri < 60:
                    return float(li) * 60.0 + float(ri)
            return float(t)
        except ValueError:
            return float(t)
    return float(t)

# ---------- Range parsing (with optional per-range padding) ----------
def parse_ranges_with_padding(range_string: str, default_pad: float):
    if not range_string:
        return []
    s = re.sub(r'\([^)]*\)', '', range_string)  # verwijder parenthetische annotaties
    pieces = [p.strip() for p in re.split(r'[,\n]+', s) if p.strip()]
    ranges = []
    rng_re = re.compile(r'(\d+(?::\d+){0,2}(?:\.\d+)?)[\s\-–—]+(\d+(?::\d+){0,2}(?:\.\d+)?)')
    pad_re_list = [
        re.compile(r'\|\s*([0-9]+(?:\.\d+)?)'),                 # |0.2
        re.compile(r'pad[:=]?\s*([0-9]+(?:\.\d+)?)', re.I),     # pad:0.2 of pad=0.2 of pad 0.2
        re.compile(r'\bp[:=]?\s*([0-9]+(?:\.\d+)?)\b', re.I),   # p:0.2 of p=0.2
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
st.set_page_config(page_title="Fragmentenspeler - YouTube", layout="centered")
st.markdown("<h1 style='margin-bottom:0.2rem'>YouTube Fragmentenspeler — Speel alleen opgegeven fragmentdelen af.</h1>", unsafe_allow_html=True)
st.markdown(
    """
Plak een YouTube-link en geef aan welke delen je in het fragment wil (gescheiden door komma's of nieuwe regels). Vul bij "Delen om te knippen" eventueel in welke delen eruit moeten. Je kunt beide tegelijk invullen, dan wordt
Voorbeelden van ondersteunde tijdformaten:
- 0.02-0.05  (decimale seconden)
- 1:03-1:20  (MM:SS)
- 01:04- 3.04
Extra tekst zoals "(knip)" of andere annotaties wordt automatisch genegeerd.
""".strip()
)
# Layout: links invoer, rechts opties
col1, col2 = st.columns([2, 1])
with col1:
    url = st.text_input("YouTube URL", placeholder="https://www.youtube.com/watch?v=VIDEO_ID")
    ranges_input = st.text_area(
        "Fragmentdelen om af te spelen (met komma of nieuwe regel gescheiden).",
        value="0.00-14.43",
        height=160
    )
    cuts_input = st.text_area(
        "Delen om te knippen (worden verwijderd uit het fragment)",
        value="3.12-3.32 (knip)\n04.44-05.21 (knip)",
        height=120,
    )
with col2:
    st.markdown("Opties")
    autoplay = st.checkbox("Speel bij klikken op 'Open speler' automatisch af (kan door browser worden geblokkeerd).", value=True)
    loop = st.checkbox("Speel fragmenten eindeloos af in een loop", value=True)
    merge_adjacent = True  # altijd aan
st.write("")  # kleine ruimte
open_player = st.button("Open speler")
if open_player:
    if not url.strip():
        st.error("Voer een YouTube-URL in.")
    else:
        video_id = extract_yt_id(url)
        if not video_id:
            st.error("Kon geen YouTube-video-ID uit deze URL halen. Controleer de URL.")
        else:
            try:
                base_pad = 1.0
                parsed_ranges = parse_ranges_with_padding(ranges_input, base_pad)
                parsed_cuts = parse_ranges_simple(cuts_input)
                if not parsed_ranges:
                    st.error("Geen geldige bereiken geparseerd. Voer minimaal één bereik in om af te spelen.")
                else:
                    if merge_adjacent:
                        parsed_ranges = merge_intervals_with_pad(parsed_ranges)
                        parsed_cuts = merge_intervals(parsed_cuts)
                    final_segments = subtract_cuts_from_padded_segments(parsed_ranges, parsed_cuts)
                    if not final_segments:
                        st.error("Er blijven geen fragmenten over na het toepassen van de knipsels, is de input correct?.")
                    else:
                        segments_json = json.dumps([[float(s), float(e), float(p)] for (s, e, p) in final_segments])
                        autoplay_flag = "true" if autoplay else "false"
                        loop_flag = "true" if loop else "false"
                        loop_checked = "checked" if loop else ""
                        autoplay_text = str(autoplay).lower()
                        # HTML/JS bouwen zonder f-string om problemen met '{' '}' te vermijden
                        html = """
<!doctype html>
<html>
  <head>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
      :root {
        --bg: #ffffff;
        --card: #fbfbfb;
        --muted: #6b7280;
        --accent: #1a73e8;
        --radius: 10px;
        --shadow: 0 6px 20px rgba(18, 18, 18, 0.06);
      }
      body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial; margin: 12px; background: var(--bg); color: #111827; }
      #container { max-width: 980px; margin: 0 auto; }
      .card { background: var(--card); border-radius: var(--radius); padding: 12px; box-shadow: var(--shadow); }
      #player { width: 100%; aspect-ratio: 16/9; border-radius: 8px; overflow: hidden; background: #000; }
      #controls { margin-top: 12px; display:flex; flex-wrap:wrap; gap:8px; align-items:center; }
      button {
        background: linear-gradient(180deg,#ffffff,#f4f4f4);
        border: 1px solid #e6e7ea;
        padding: 8px 14px;
        border-radius: 8px;
        cursor: pointer;
        font-size: 14px;
      }
      button.primary {
        background: linear-gradient(180deg,var(--accent), #1666c3);
        color: white;
        border: none;
      }
      .pad-input {
        width:72px;
        padding:4px 6px;
        border-radius:6px;
        border:1px solid #ddd;
        font-size:13px;
        margin-left:8px;
      }
      .seg-row { display:flex; align-items:center; gap:8px; margin-bottom:6px; }
      .seg-times { font-family: monospace; }
      .muted { color: var(--muted); font-size:13px; margin-left:auto; }
      .hint { color: var(--muted); font-size:13px; margin-top:6px; }
      .current { color: var(--accent); font-weight:700; margin-left:6px; }
    </style>
  </head>
  <body>
    <div id="container" class="card">
      <div id="player"></div>
      <div id="controls">
        <button id="playAll" class="primary">Speel Fragmenten af</button>
        <button id="pause">Pauzeer</button>
        <button id="prev">Vorige</button>
        <button id="next">Volgende</button>
        <label style="margin-left:8px;">
          <input type="checkbox" id="loop" {loop_checked}> Herhalen/loopen
        </label>
        <div class="muted">Poging automatisch afspelen: {autoplay_text}</div>
      </div>
      <div id="segments" class="card" style="margin-top:12px;"></div>
      <div class="hint">Klik in het opvulvakje en wijzig de waarde om de padding voor dat segment aan te passen. Wijzigingen worden onmiddellijk toegepast.</div>
    </div>
    <script>
      var videoId = "{video_id}";
      var segments = {segments_json};
      var currentIndex = 0;
      var checkInterval = null;
      var player = null;
      var userLoop = {loop_flag};
      var autoplay = {autoplay_flag};
      function secondsToString(s) {
        var total = Number(s);
        if (!isFinite(total)) return String(s);
        var h = Math.floor(total / 3600);
        var m = Math.floor((total % 3600) / 60);
        var sec = total % 60;
        var secInt = Math.floor(sec);
        var frac = sec - secInt;
        var fracStr = (frac > 0) ? frac.toFixed(2).substring(1) : '.00';
        var secStr = String(secInt).padStart(2, '0') + fracStr;
        if (h > 0) {
          return h + ':' + String(m).padStart(2, '0') + ':' + secStr;
        }
        return String(m) + ':' + secStr;
      }
      function renderSegments() {
        var el = document.getElementById('segments');
        var html = '<b>Segmenten:</b><div style="margin-top:8px">';
        for (var i=0;i<segments.length;i++) {
          var s = secondsToString(segments[i][0]);
          var e = secondsToString(segments[i][1]);
          var p = (segments[i].length > 2) ? Number(segments[i][2]).toFixed(1) : '1.0';
          var currentMark = (i === currentIndex) ? '<span class="current">(huidig)</span>' : '';
          html += '<div class="seg-row">';
          html += '<div class="seg-times"><strong>' + (i+1) + '.</strong>&nbsp;&nbsp;' + s + ' → ' + e + '</div>';
          html += '<div style="margin-left:6px;color:#6b7280;">(pad: </div>';
          html += '<input type="number" min="0" max="5" step="0.1" class="pad-input" id="pad_' + i + '" value="' + p + '" oninput="onPadInput(' + i + ', this.value)">';
          html += '<div style="color:#6b7280;">s)</div>';
          html += currentMark;
          html += '</div>';
        }
        html += '</div>';
        el.innerHTML = html;
      }
      function onPadInput(idx, val) {
        var v = parseFloat(val);
        if (!isFinite(v) || v < 0) v = 0;
        if (v > 5) v = 5;
        if (!Array.isArray(segments[idx])) return;
        segments[idx][2] = v;
        var inp = document.getElementById('pad_' + idx);
        if (inp) inp.value = v.toFixed(1);
      }
      (function() {
        var tag = document.createElement('script');
        tag.src = "https://www.youtube.com/iframe_api";
        document.head.appendChild(tag);
      })();
      function onYouTubeIframeAPIReady() {
        player = new YT.Player('player', {
          height: '390',
          width: '640',
          videoId: videoId,
          playerVars: {
            'rel': 0,
            'modestbranding': 1,
            'controls': 1,
            'autoplay': autoplay ? 1 : 0
          },
          events: {
            'onReady': onPlayerReady,
            'onStateChange': onPlayerStateChange
          }
        });
      }
      function onPlayerReady(event) {
        renderSegments();
        if (autoplay) {
          setTimeout(function() {
            userLoop = document.getElementById('loop').checked;
            playSegment(0);
          }, 250);
        }
      }
      function onPlayerStateChange(event) {
      }
      function playSegment(idx) {
        if (!player) return;
        if (idx < 0) idx = 0;
        if (idx >= segments.length) {
          if (userLoop) {
            idx = 0;
          } else {
            try { player.pauseVideo(); } catch(e) {}
            return;
          }
        }
        currentIndex = idx;
        var start = Number(segments[currentIndex][0]);
        var end = Number(segments[currentIndex][1]);
        var segLength = Math.max(0.0, end - start);
        if (checkInterval) {
          clearInterval(checkInterval);
          checkInterval = null;
        }
        setTimeout(function() {
          try { player.seekTo(start, true); } catch(e) { console.warn(e); }
          try { player.playVideo(); } catch(e) { console.warn(e); }
          setTimeout(function() {
            try { player.playVideo(); } catch(e) { }
          }, 120);
        }, 40);
        var checkFreq = segLength < 0.25 ? 30 : 100;
        checkInterval = setInterval(function() {
          if (!player || typeof player.getCurrentTime !== 'function') return;
          var now = player.getCurrentTime();
          var rawPad = (segments[currentIndex] && segments[currentIndex].length > 2) ? Number(segments[currentIndex][2]) : 1.0;
          if (!isFinite(rawPad) || rawPad < 0) rawPad = 0;
          var padding = rawPad;
          if (segLength > 0) {
            padding = Math.min(padding, Math.max(0.001, segLength / 4));
          } else {
            padding = Math.max(0.001, padding);
          }
          var effectiveEnd = end + padding;
          var tolerance = Math.max(0.005, Math.min(0.05, padding * 0.5));
          var padInput = document.getElementById('pad_' + currentIndex);
          if (padInput) padInput.value = padding.toFixed(1);
          if (now >= start - 0.02 && now >= (effectiveEnd - tolerance)) {
            clearInterval(checkInterval);
            checkInterval = null;
            currentIndex += 1;
            if (currentIndex < segments.length) {
              playSegment(currentIndex);
            } else {
              if (userLoop) {
                playSegment(0);
              } else {
                try { player.pauseVideo(); } catch(e) { }
              }
            }
            renderSegments();
          }
        }, checkFreq);
        renderSegments();
      }
      document.getElementById('playAll').addEventListener('click', function() {
        userLoop = document.getElementById('loop').checked;
        playSegment(0);
      });
      document.getElementById('pause').addEventListener('click', function() {
        if (player) try { player.pauseVideo(); } catch(e) { }
        if (checkInterval) clearInterval(checkInterval);
        checkInterval = null;
      });
      document.getElementById('next').addEventListener('click', function() {
        if (checkInterval) clearInterval(checkInterval);
        checkInterval = null;
        playSegment(currentIndex + 1);
      });
      document.getElementById('prev').addEventListener('click', function() {
        if (checkInterval) clearInterval(checkInterval);
        checkInterval = null;
        playSegment(Math.max(0, currentIndex - 1));
      });
      document.addEventListener('keydown', function(e) {
        if (e.key === ' ') {
          if (player) {
            var state = player.getPlayerState();
            if (state === YT.PlayerState.PLAYING) {
              try { player.pauseVideo(); } catch(e) { }
              if (checkInterval) clearInterval(checkInterval);
              checkInterval = null;
            } else {
              try { player.playVideo(); } catch(e) { }
            }
          }
          e.preventDefault();
        } else if (e.key === 'n') {
          document.getElementById('next').click();
        } else if (e.key === 'p') {
          document.getElementById('prev').click();
        }
      });
    </script>
  </body>
</html>
"""
                        html = html.replace("{video_id}", video_id)
                        html = html.replace("{segments_json}", segments_json)
                        html = html.replace("{loop_flag}", loop_flag)
                        html = html.replace("{autoplay_flag}", autoplay_flag)
                        html = html.replace("{loop_checked}", loop_checked)
                        html = html.replace("{autoplay_text}", autoplay_text)
                        st.components.v1.html(html, height=720, scrolling=True)
            except Exception as e:
                st.error(f"Kon bereiken niet parsen of knipsels niet toepassen: {e}")
