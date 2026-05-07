"""
재재프로 카카오톡 뉴스 브리핑 봇 — Hugging Face Spaces 배포용
완전 무료 | 카카오 API | PC 없이 폰에서 사용 가능
"""
import os, json, threading, time, datetime, re, warnings, schedule
import requests
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
import gradio as gr
from fastapi import FastAPI
from fastapi.responses import RedirectResponse, HTMLResponse
from google.genai import Client

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

# ─── 설정 ─────────────────────────────────────────────────────────────────────
KAKAO_REST_KEY = os.environ.get("KAKAO_REST_API_KEY", "")
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")
# HF Spaces는 SPACE_HOST 환경변수를 자동 제공
_host = os.environ.get("SPACE_HOST", "localhost:7860")
BASE_URL = f"https://{_host}" if not _host.startswith("http") else _host
REDIRECT_URI = f"{BASE_URL}/auth/callback"

# ─── 토큰 (환경변수 또는 메모리 저장) ────────────────────────────────────────
tokens = {
    "access_token":  os.environ.get("KAKAO_ACCESS_TOKEN", ""),
    "refresh_token": os.environ.get("KAKAO_REFRESH_TOKEN", ""),
}

# ─── 로그 ─────────────────────────────────────────────────────────────────────
logs = []
def add_log(msg):
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    logs.append(f"[{ts}] {msg}"); print(f"[{ts}] {msg}")
def get_logs(): return "\n".join(logs[-60:])

# ─── Kakao OAuth ──────────────────────────────────────────────────────────────
def kakao_auth_url():
    return (f"https://kauth.kakao.com/oauth/authorize"
            f"?client_id={KAKAO_REST_KEY}&redirect_uri={REDIRECT_URI}"
            f"&response_type=code&scope=talk_message")

def exchange_code(code: str):
    res = requests.post("https://kauth.kakao.com/oauth/token", data={
        "grant_type": "authorization_code", "client_id": KAKAO_REST_KEY,
        "redirect_uri": REDIRECT_URI, "code": code})
    return res.json()

def refresh_access_token():
    if not tokens["refresh_token"]: return False
    res = requests.post("https://kauth.kakao.com/oauth/token", data={
        "grant_type": "refresh_token", "client_id": KAKAO_REST_KEY,
        "refresh_token": tokens["refresh_token"]})
    d = res.json()
    if "access_token" in d:
        tokens["access_token"] = d["access_token"]
        if "refresh_token" in d: tokens["refresh_token"] = d["refresh_token"]
        add_log("토큰 자동 갱신 완료!"); return True
    return False

def check_login():
    if not tokens["access_token"]: return "❌ 로그인 필요"
    r = requests.get("https://kapi.kakao.com/v2/user/me",
                     headers={"Authorization": f"Bearer {tokens['access_token']}"})
    if r.status_code == 200:
        name = r.json().get("properties", {}).get("nickname", "사용자")
        return f"✅ {name}님 로그인됨"
    if r.status_code == 401 and refresh_access_token(): return "✅ 토큰 갱신 완료"
    return "❌ 로그인 만료 — 재로그인 필요"

# ─── 뉴스 수집 ────────────────────────────────────────────────────────────────
def fetch_news():
    url = "https://news.google.com/rss/search?q=재개발+OR+재건축+OR+금리+OR+부동산+정책+when:1d&hl=ko&gl=KR&ceid=KR:ko"
    try:
        soup = BeautifulSoup(
            requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10).text,
            "html.parser")
        arts = []
        for item in soup.find_all("item")[:15]:
            title = item.title.text.strip()
            link  = item.link.text.strip() if item.link else ""
            date  = ""
            pub   = item.find("pubdate")
            if pub:
                try:
                    from email.utils import parsedate_to_datetime
                    dt = parsedate_to_datetime(pub.text.strip()).astimezone(
                        datetime.timezone(datetime.timedelta(hours=9)))
                    date = dt.strftime("%m/%d %H:%M")
                except Exception: pass
            arts.append({"title": title, "link": link, "date": date})
        return arts
    except Exception as e:
        add_log(f"뉴스 수집 에러: {e}"); return []

def fetch_weather():
    try:
        soup = BeautifulSoup(requests.get(
            "https://search.naver.com/search.naver?query=서울날씨",
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"},
            timeout=5).text, "html.parser")
        temp    = soup.find("div", class_="temperature_text")
        summary = soup.find("span", class_="weather before_slash")
        min_max = soup.find("p", class_="temperature_info")
        dust = " | ".join(
            f"{d.find('em').text.strip()} {d.find('strong').text.strip()}"
            for d in soup.select(".today_chart_list .item_today")
            if d.find("em") and d.find("strong"))
        parts = []
        if temp:    parts.append(temp.text.strip())
        if summary: parts.append(summary.text.strip())
        if min_max: parts.append(f"(최저/최고: {min_max.text.strip()})")
        if dust:    parts.append(f"| {dust}")
        return f"☁️ 오늘 서울 날씨: {' '.join(parts)}" if parts else "☁️ 날씨 조회 실패"
    except Exception: return "☁️ 날씨 조회 실패"

# ─── AI 브리핑 ────────────────────────────────────────────────────────────────
def make_briefing(articles):
    weather   = fetch_weather()
    arts_text = "\n\n".join(f"- {a['title']}\n  {a['link']}" for a in articles)
    prompt    = f"""부동산 전문 AI 비서로서 아래 뉴스와 날씨로 카카오톡 단톡방용 브리핑을 작성하세요.
형식: "🌞 정프로가 전하는 오늘의 재재 뉴스 🌞"
- 이모지 활용, 날씨 포함, 뉴스 제목+링크 포함, 트렌드 1~2줄 요약
[날씨] {weather}
[뉴스] {arts_text}
마크다운 없이 카카오톡 텍스트 형태로."""
    try:
        resp = Client(api_key=GOOGLE_API_KEY).models.generate_content(
            model="gemini-2.0-flash", contents=prompt)
        add_log("AI 요약 완료!"); return resp.text.strip()
    except Exception as e:
        add_log(f"AI 에러: {e}")
        return f"🌞 정프로가 전하는 오늘의 재재 뉴스 🌞\n\n{weather}\n\n{arts_text}"

# ─── 카카오 '나에게 보내기' ────────────────────────────────────────────────────
def send_to_me(text):
    if not tokens["access_token"]: return "❌ 로그인 필요"
    res = requests.post(
        "https://kapi.kakao.com/v2/api/talk/memo/default/send",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
        data={"template_object": json.dumps({
            "object_type": "text", "text": text[:1999],
            "link": {"web_url": "https://news.google.com",
                     "mobile_web_url": "https://news.google.com"}})})
    if res.status_code == 200:
        add_log("'나에게 보내기' 완료!"); return "✅ 카카오톡 '나와의 채팅'으로 발송 완료!"
    if res.status_code == 401 and refresh_access_token(): return send_to_me(text)
    add_log(f"발송 실패: {res.text}")
    return f"❌ 발송 실패: {res.json().get('msg', res.text)}"

# ─── 스케줄러 ─────────────────────────────────────────────────────────────────
sch_running = False

def start_scheduler(send_time):
    global sch_running
    if sch_running: return "이미 실행 중", get_logs()
    raw = send_time.strip()
    for p, fn in [
        (r"^(\d{1,2})시$",           lambda m: f"{int(m[1]):02d}:00"),
        (r"^(\d{1,2})시\s*(\d{1,2})분$", lambda m: f"{int(m[1]):02d}:{int(m[2]):02d}"),
        (r"^(\d{1,2}):(\d{1,2})$",   lambda m: f"{int(m[1]):02d}:{int(m[2]):02d}"),
    ]:
        m = re.match(p, raw)
        if m: raw = fn(m.groups()); break
    try:
        schedule.clear()
        schedule.every().day.at(raw).do(lambda: add_log(send_to_me(make_briefing(fetch_news()))))
    except Exception as e: return f"시간 오류: {e}", get_logs()
    sch_running = True
    def _loop():
        while sch_running:
            schedule.run_pending(); time.sleep(1)
    threading.Thread(target=_loop, daemon=True).start()
    add_log(f"스케줄러 시작: 매일 {raw}")
    return f"✅ 매일 {raw} 자동 발송 예약!", get_logs()

def stop_scheduler():
    global sch_running
    sch_running = False; schedule.clear(); add_log("스케줄러 중지")
    return "⛔ 스케줄러 중지됨", get_logs()

# ─── Gradio UI 핸들러 ─────────────────────────────────────────────────────────
fetched = []

def ui_login_url():
    url = kakao_auth_url()
    return url, f"위 URL을 복사해서 브라우저에서 열어주세요.\n로그인 완료 후 아래 [로그인 상태 확인] 버튼을 누르세요."

def ui_check_login():
    return check_login(), get_logs()

def ui_fetch():
    global fetched
    add_log("뉴스 불러오는 중...")
    fetched = fetch_news()
    if not fetched:
        return gr.CheckboxGroup(choices=[]), "⚠️ 뉴스 수집 실패", get_logs()
    choices = [f"[{i+1}] {a['date']}  {a['title']}" for i, a in enumerate(fetched)]
    add_log(f"{len(fetched)}건 수집!")
    return gr.CheckboxGroup(choices=choices, value=choices), f"✅ {len(fetched)}건 수집!", get_logs()

def ui_send(selected):
    if not selected: return "⚠️ 기사를 선택하세요.", get_logs()
    arts = []
    for label in selected:
        m = re.match(r"^\[(\d+)\]", label)
        if m:
            idx = int(m.group(1)) - 1
            if 0 <= idx < len(fetched): arts.append(fetched[idx])
    if not arts: return "⚠️ 선택 오류. 뉴스를 다시 불러오세요.", get_logs()
    def job():
        add_log(f"{len(arts)}건 AI 브리핑 생성 중...")
        add_log(send_to_me(make_briefing(arts)))
    threading.Thread(target=job, daemon=True).start()
    return f"🚀 {len(arts)}건 발송 시작! '나와의 채팅' 확인!", get_logs()

# ─── FastAPI OAuth 콜백 ───────────────────────────────────────────────────────
fast_app = FastAPI()

@fast_app.get("/auth/callback")
async def oauth_callback(code: str = None, error: str = None):
    if error or not code:
        return HTMLResponse("<h2>❌ 로그인 실패. 다시 시도해주세요.</h2>")
    data = exchange_code(code)
    if "access_token" in data:
        tokens["access_token"]  = data["access_token"]
        tokens["refresh_token"] = data.get("refresh_token", "")
        add_log("✅ 카카오 로그인 성공!")
        return HTMLResponse("""
        <html><body style="font-family:sans-serif;text-align:center;padding:50px">
        <h2>✅ 카카오 로그인 성공!</h2>
        <p>이 창을 닫고 봇 화면으로 돌아가세요.</p>
        <script>setTimeout(()=>window.close(),3000)</script>
        </body></html>""")
    return HTMLResponse(f"<h2>❌ 토큰 발급 실패: {data.get('error_description','알 수 없는 오류')}</h2>")

# ─── Gradio UI ────────────────────────────────────────────────────────────────
with gr.Blocks(title="재재프로 뉴스 봇", theme=gr.themes.Soft()) as demo:
    gr.Markdown("# 🌞 재재프로 카카오톡 뉴스 브리핑 봇")
    gr.Markdown("📱 **핸드폰에서도 사용 가능** | 완전 무료 | PC 없이 카카오 API로 직접 발송!")

    with gr.Tabs():
        # ── 탭 1: 로그인 ─────────────────────────────────────────────────────
        with gr.Tab("🔑 카카오 로그인"):
            gr.Markdown("""
### 최초 1회 로그인 (이후 자동 유지)
1. **[로그인 URL 생성]** 버튼 클릭
2. URL을 복사 → 브라우저에서 열기 → 카카오 로그인
3. "로그인 성공!" 메시지 확인 후 이 창으로 돌아오기
4. **[로그인 상태 확인]** 클릭
""")
            login_url_box = gr.Textbox(label="카카오 로그인 URL (복사 후 새 탭에서 열기)", lines=2, interactive=False)
            login_hint    = gr.Textbox(label="안내", interactive=False)
            with gr.Row():
                url_btn   = gr.Button("🟡 로그인 URL 생성", variant="primary", size="lg")
                check_btn = gr.Button("🔄 로그인 상태 확인", size="lg")
            login_status = gr.Textbox(label="로그인 상태", interactive=False)
            login_log    = gr.Textbox(label="로그", lines=3, interactive=False)

            url_btn.click(fn=ui_login_url, inputs=[], outputs=[login_url_box, login_hint])
            check_btn.click(fn=ui_check_login, inputs=[], outputs=[login_status, login_log])

        # ── 탭 2: 뉴스 선택 발송 ─────────────────────────────────────────────
        with gr.Tab("📰 뉴스 선택 발송"):
            gr.Markdown("뉴스를 불러오고 원하는 기사만 체크 → AI 요약 후 **카카오톡 '나와의 채팅'** 으로 발송!")
            fetch_btn   = gr.Button("🔍 오늘의 뉴스 불러오기", variant="primary")
            article_chk = gr.CheckboxGroup(choices=[], label="기사 목록 (날짜 | 제목)", interactive=True)
            send_btn    = gr.Button("✉️ 선택 기사 카카오톡 발송", variant="primary")
            status_box  = gr.Textbox(label="상태", interactive=False)
            log_box     = gr.Textbox(label="실행 로그", lines=8, interactive=False)
            gr.Button("🔄 로그 새로고침").click(fn=get_logs, inputs=[], outputs=log_box)

            fetch_btn.click(fn=ui_fetch, inputs=[], outputs=[article_chk, status_box, log_box])
            send_btn.click(fn=ui_send, inputs=[article_chk], outputs=[status_box, log_box])

        # ── 탭 3: 자동 발송 ──────────────────────────────────────────────────
        with gr.Tab("⏰ 매일 자동 발송"):
            gr.Markdown("""
매일 지정 시간에 **'나와의 채팅'** 으로 뉴스 브리핑 자동 발송.
받은 메시지를 단톡방에 **공유(전달)** 하시면 됩니다!
> ⚠️ Hugging Face Spaces가 재시작되면 스케줄러가 초기화됩니다.
""")
            sch_time   = gr.Textbox(label="발송 시간", placeholder="예: 08:00 또는 8시", value="08:00")
            sch_status = gr.Textbox(label="스케줄러 상태", interactive=False)
            sch_log    = gr.Textbox(label="로그", lines=5, interactive=False)
            with gr.Row():
                gr.Button("▶ 스케줄러 시작", variant="primary").click(
                    fn=start_scheduler, inputs=[sch_time], outputs=[sch_status, sch_log])
                gr.Button("⏹ 중지", variant="stop").click(
                    fn=stop_scheduler, inputs=[], outputs=[sch_status, sch_log])

# ─── FastAPI에 Gradio 마운트 후 실행 ─────────────────────────────────────────
import uvicorn
app = gr.mount_gradio_app(fast_app, demo, path="/")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 7860))
    uvicorn.run(app, host="0.0.0.0", port=port)
