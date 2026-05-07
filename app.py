"""
재재프로 카카오톡 뉴스 브리핑 봇 — 클라우드 버전
Railway 배포용 | Kakao API 사용 (pyautogui 없음, PC 불필요)
"""
import os, json, threading, time, datetime, re, warnings, schedule
from pathlib import Path
import requests
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
import gradio as gr
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from google.genai import Client

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

# ─── 설정 ────────────────────────────────────────────────────────────────────
KAKAO_REST_KEY = os.environ.get("KAKAO_REST_API_KEY", "416a6a068e410e45b959ea81fc14e9cf")
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")
BASE_URL       = os.environ.get("BASE_URL", "http://localhost:7860")
REDIRECT_URI   = f"{BASE_URL}/auth/callback"

# ─── 토큰 저장 ───────────────────────────────────────────────────────────────
TOKEN_FILE = Path("tokens.json")
tokens = {"access_token": os.environ.get("KAKAO_ACCESS_TOKEN", ""),
          "refresh_token": os.environ.get("KAKAO_REFRESH_TOKEN", "")}

def save_tokens():
    TOKEN_FILE.write_text(json.dumps(tokens))

if TOKEN_FILE.exists():
    try:
        tokens.update(json.loads(TOKEN_FILE.read_text()))
    except Exception:
        pass

# ─── 로그 ────────────────────────────────────────────────────────────────────
logs = []
def add_log(msg):
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    logs.append(f"[{ts}] {msg}"); print(f"[{ts}] {msg}")
def get_logs():
    return "\n".join(logs[-60:])

# ─── Kakao OAuth ─────────────────────────────────────────────────────────────
def kakao_auth_url():
    return (f"https://kauth.kakao.com/oauth/authorize"
            f"?client_id={KAKAO_REST_KEY}&redirect_uri={REDIRECT_URI}"
            f"&response_type=code&scope=talk_message")

def refresh_token():
    if not tokens["refresh_token"]: return False
    res = requests.post("https://kauth.kakao.com/oauth/token", data={
        "grant_type": "refresh_token", "client_id": KAKAO_REST_KEY,
        "refresh_token": tokens["refresh_token"]})
    d = res.json()
    if "access_token" in d:
        tokens["access_token"] = d["access_token"]
        if "refresh_token" in d: tokens["refresh_token"] = d["refresh_token"]
        save_tokens(); add_log("토큰 갱신 완료"); return True
    return False

def check_login():
    if not tokens["access_token"]: return "❌ 로그인 필요"
    r = requests.get("https://kapi.kakao.com/v2/user/me",
                     headers={"Authorization": f"Bearer {tokens['access_token']}"})
    if r.status_code == 200:
        name = r.json().get("properties", {}).get("nickname", "사용자")
        return f"✅ {name} 로그인됨"
    if r.status_code == 401 and refresh_token(): return "✅ 토큰 갱신 완료"
    return "❌ 로그인 만료"

# ─── 뉴스 수집 ────────────────────────────────────────────────────────────────
def fetch_news():
    url = "https://news.google.com/rss/search?q=재개발+OR+재건축+OR+금리+OR+부동산+정책+when:1d&hl=ko&gl=KR&ceid=KR:ko"
    try:
        soup = BeautifulSoup(requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10).text, "html.parser")
        arts = []
        for item in soup.find_all("item")[:15]:
            title = item.title.text.strip()
            link  = item.link.text.strip() if item.link else ""
            pub = item.find("pubdate")
            date = ""
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
    weather = fetch_weather()
    arts_text = "\n\n".join(f"- {a['title']}\n  {a['link']}" for a in articles)
    prompt = f"""부동산 전문 AI 비서로서 아래 뉴스와 날씨로 카카오톡 단톡방용 브리핑을 작성하세요.
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
        add_log(f"AI 요약 에러: {e}")
        return f"🌞 정프로가 전하는 오늘의 재재 뉴스 🌞\n\n{weather}\n\n{arts_text}"

# ─── 카카오 '나에게 보내기' ────────────────────────────────────────────────────
def send_to_me(text):
    if not tokens["access_token"]: return "❌ 카카오 로그인 먼저 해주세요."
    res = requests.post(
        "https://kapi.kakao.com/v2/api/talk/memo/default/send",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
        data={"template_object": json.dumps({
            "object_type": "text", "text": text[:1999],
            "link": {"web_url": "https://news.google.com", "mobile_web_url": "https://news.google.com"}
        })})
    if res.status_code == 200:
        add_log("카카오톡 '나에게 보내기' 완료!"); return "✅ 카카오톡 '나와의 채팅'으로 발송 완료!"
    if res.status_code == 401 and refresh_token(): return send_to_me(text)
    add_log(f"발송 실패: {res.text}")
    return f"❌ 발송 실패: {res.json().get('msg', res.text)}"

# ─── 스케줄러 ─────────────────────────────────────────────────────────────────
sch_running = False
def start_scheduler(send_time):
    global sch_running
    if sch_running: return "이미 실행 중", get_logs()
    raw = send_time.strip()
    for p, r in [(r"^(\d{1,2})시$", lambda m: f"{int(m[1]):02d}:00"),
                 (r"^(\d{1,2})시\s*(\d{1,2})분$", lambda m: f"{int(m[1]):02d}:{int(m[2]):02d}"),
                 (r"^(\d{1,2}):(\d{1,2})$", lambda m: f"{int(m[1]):02d}:{int(m[2]):02d}")]:
        m = re.match(p, raw)
        if m: raw = r(m.groups()); break
    try: schedule.clear(); schedule.every().day.at(raw).do(lambda: send_to_me(make_briefing(fetch_news())))
    except Exception as e: return f"시간 오류: {e}", get_logs()
    sch_running = True
    threading.Thread(target=lambda: [schedule.run_pending() or time.sleep(1) for _ in iter(lambda: not sch_running, True)], daemon=True).start()
    add_log(f"스케줄러 시작: 매일 {raw}")
    return f"✅ 매일 {raw} 자동 발송!", get_logs()

def stop_scheduler():
    global sch_running
    sch_running = False; schedule.clear(); add_log("스케줄러 중지")
    return "⛔ 중지됨", get_logs()

# ─── Gradio UI 핸들러 ─────────────────────────────────────────────────────────
fetched = []
def ui_fetch():
    global fetched
    fetched = fetch_news()
    if not fetched: return gr.CheckboxGroup(choices=[]), "⚠️ 뉴스 수집 실패", get_logs()
    choices = [f"[{i+1}] {a['date']} {a['title']}" for i, a in enumerate(fetched)]
    add_log(f"{len(fetched)}건 수집!")
    return gr.CheckboxGroup(choices=choices, value=choices), f"✅ {len(fetched)}건 수집!", get_logs()

def ui_send(selected):
    if not selected: return "⚠️ 기사를 선택하세요.", get_logs()
    arts = [fetched[int(re.match(r"^\[(\d+)\]", l).group(1))-1] for l in selected if re.match(r"^\[(\d+)\]", l)]
    def job():
        add_log(f"{len(arts)}건 AI 브리핑 중...")
        result = send_to_me(make_briefing(arts)); add_log(result)
    threading.Thread(target=job, daemon=True).start()
    return f"🚀 {len(arts)}건 발송 시작! '나와의 채팅' 확인하세요.", get_logs()

# ─── FastAPI OAuth 콜백 ───────────────────────────────────────────────────────
fast_app = FastAPI()

@fast_app.get("/auth/callback")
async def oauth_callback(code: str = None, error: str = None):
    if error or not code: return RedirectResponse("/?error=auth_failed")
    res = requests.post("https://kauth.kakao.com/oauth/token", data={
        "grant_type": "authorization_code", "client_id": KAKAO_REST_KEY,
        "redirect_uri": REDIRECT_URI, "code": code})
    data = res.json()
    if "access_token" in data:
        tokens["access_token"]  = data["access_token"]
        tokens["refresh_token"] = data.get("refresh_token", "")
        save_tokens(); add_log("✅ 카카오 로그인 성공!")
        return RedirectResponse("/")
    return RedirectResponse("/?error=token_failed")

# ─── Gradio UI ────────────────────────────────────────────────────────────────
with gr.Blocks(title="재재프로 뉴스 봇", theme=gr.themes.Soft()) as demo:
    gr.Markdown("# 🌞 재재프로 카카오톡 뉴스 브리핑 봇")
    gr.Markdown("📱 **핸드폰에서도 사용 가능** | PC 없이 카카오 API로 직접 발송!")

    with gr.Tabs():
        with gr.Tab("🔑 카카오 로그인"):
            gr.Markdown("""
### 최초 1회 로그인 필요
1. **[로그인 URL 생성]** 클릭 → URL 복사
2. 카카오톡 앱에서 URL 열기 → 로그인 승인
3. **[로그인 상태 확인]** 클릭
""")
            login_url_out = gr.Textbox(label="카카오 로그인 URL (복사 후 브라우저에서 열기)", interactive=False, lines=3)
            login_url_btn = gr.Button("🟡 로그인 URL 생성", variant="primary", size="lg")
            login_status  = gr.Textbox(label="로그인 상태", interactive=False)
            check_btn     = gr.Button("🔄 로그인 상태 확인")

            login_url_btn.click(fn=kakao_auth_url, inputs=[], outputs=login_url_out)
            check_btn.click(fn=lambda: (check_login(), get_logs()), inputs=[], outputs=[login_status, gr.Textbox(visible=False)])

        with gr.Tab("📰 뉴스 선택 발송"):
            fetch_btn   = gr.Button("🔍 오늘의 뉴스 불러오기", variant="primary")
            article_chk = gr.CheckboxGroup(choices=[], label="기사 목록 (보낼 기사만 체크)", interactive=True)
            send_btn    = gr.Button("✉️ 선택 기사 카카오톡 발송 (나와의 채팅)", variant="primary")
            status_box  = gr.Textbox(label="상태", interactive=False)
            log_box     = gr.Textbox(label="실행 로그", lines=8, interactive=False)
            gr.Button("🔄 로그 새로고침").click(fn=get_logs, inputs=[], outputs=log_box)

            fetch_btn.click(fn=ui_fetch, inputs=[], outputs=[article_chk, status_box, log_box])
            send_btn.click(fn=ui_send, inputs=[article_chk], outputs=[status_box, log_box])

        with gr.Tab("⏰ 매일 자동 발송"):
            gr.Markdown("매일 지정 시간에 **나와의 채팅**으로 브리핑 자동 발송. 받으면 단톡방에 공유하세요!")
            sch_time = gr.Textbox(label="발송 시간", placeholder="예: 08:00 또는 8시", value="08:00")
            with gr.Row():
                gr.Button("▶ 스케줄러 시작", variant="primary").click(
                    fn=start_scheduler, inputs=[sch_time], outputs=[gr.Textbox(label="상태"), gr.Textbox(label="로그", lines=5)])
                gr.Button("⏹ 중지", variant="stop").click(
                    fn=stop_scheduler, inputs=[], outputs=[gr.Textbox(label="상태"), gr.Textbox(label="로그", lines=5)])

import uvicorn
app = gr.mount_gradio_app(fast_app, demo, path="/")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 7860))
    uvicorn.run(app, host="0.0.0.0", port=port)
