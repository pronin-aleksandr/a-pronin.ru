#!/usr/bin/env python3
"""
Pronin Monitor v7 — Claude AI + голос + очередь
"""

import requests
from bs4 import BeautifulSoup
import json
import os
import hashlib
import re
import tempfile
from datetime import datetime
import base64

# ── Конфиг ──────────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID        = os.environ.get('CHAT_ID')
VK_TOKEN       = os.environ.get('VK_TOKEN')
GITHUB_TOKEN   = os.environ.get('GITHUB_TOKEN')
GEMINI_KEY     = os.environ.get('GEMINI_API_KEY')
GITHUB_REPO    = 'pronin-aleksandr/a-pronin.ru'
SITE_URL       = 'https://a-pronin.ru'
INDEX_FILE     = 'index.html'

WEB_PAGES = [
    {'url': 'https://bitobe.ru/team/4573/', 'section': 'Консалтинг', 'keywords': ['Пронин']},
    {'url': 'https://fgd.spb.ru/',          'section': 'Дроны',      'keywords': ['Пронин']},
]

VK_PAGES = [
    {'screen_name': 'sansei4',  'section': 'Консалтинг/Дроны', 'keywords': ['Пронин','BITOBE','дрон','FPV','БПЛА']},
    {'screen_name': 'bitobe',   'section': 'Консалтинг',       'keywords': ['Пронин']},
    {'screen_name': 'fgdrus',   'section': 'Дроны',            'keywords': ['Пронин']},
    {'screen_name': 'fgdpskov', 'section': 'Дроны',            'keywords': ['Пронин']},
    {'screen_name': 'fgdspb',   'section': 'Дроны',            'keywords': ['Пронин']},
]

STATE_FILE   = 'data/state.json'
PENDING_FILE = 'data/pending.json'
QUEUE_FILE   = 'data/queue.json'
UPDATES_FILE = 'data/last_update_id.txt'

PLACEMENT_LABELS = {
    'news-consulting':    'Новости / Консалтинг',
    'news-drone':         'Новости / Дроны',
    'events-consulting':  'Мероприятия / Консалтинг',
    'events-drone':       'Мероприятия / Дроны',
    'profile-consulting': 'Профиль / Консалтинг',
    'profile-drone':      'Профиль / Дроны',
}


# ── Утилиты ─────────────────────────────────────────────────

def load_json(path, default):
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return default

def save_json(path, data):
    os.makedirs('data', exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_hash(text):
    return hashlib.md5(text.encode('utf-8')).hexdigest()

def fetch_page(url):
    try:
        r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=15)
        r.encoding = 'utf-8'
        return r.text
    except Exception as e:
        print(f"Ошибка {url}: {e}")
        return None

def extract_text(html):
    soup = BeautifulSoup(html, 'html.parser')
    for tag in soup(['script', 'style']):
        tag.decompose()
    return soup.get_text(separator=' ', strip=True)

def get_snippet(text, keyword, context=120):
    idx = text.lower().find(keyword.lower())
    if idx == -1:
        return None
    start = max(0, idx - context)
    end   = min(len(text), idx + len(keyword) + context)
    snippet = ' '.join(text[start:end].split())
    if start > 0:       snippet = '...' + snippet
    if end < len(text): snippet += '...'
    return snippet


# ── Telegram ────────────────────────────────────────────────

def tg(message):
    try:
        requests.post(
            f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage',
            data={'chat_id': CHAT_ID, 'text': message,
                  'parse_mode': 'HTML', 'disable_web_page_preview': True},
            timeout=10
        )
    except Exception as e:
        print(f"TG ошибка: {e}")

def tg_get_file_path(file_id):
    """Получает путь к файлу на серверах Telegram."""
    try:
        r = requests.get(
            f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/getFile',
            params={'file_id': file_id}, timeout=10
        )
        return r.json().get('result', {}).get('file_path')
    except:
        return None

def tg_download_file(file_path):
    """Скачивает файл с серверов Telegram, возвращает байты."""
    try:
        r = requests.get(
            f'https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_path}',
            timeout=30
        )
        return r.content
    except:
        return None

def get_updates(offset=None):
    try:
        params = {'timeout': 0, 'allowed_updates': ['message']}
        if offset:
            params['offset'] = offset
        r = requests.get(
            f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates',
            params=params, timeout=10
        )
        return r.json().get('result', [])
    except:
        return []

def get_last_uid():
    if os.path.exists(UPDATES_FILE):
        with open(UPDATES_FILE) as f:
            try: return int(f.read().strip())
            except: return None
    return None

def save_last_uid(uid):
    os.makedirs('data', exist_ok=True)
    with open(UPDATES_FILE, 'w') as f:
        f.write(str(uid))


# ── Распознавание голоса ─────────────────────────────────────

def transcribe_voice(file_id):
    """Скачивает голосовое сообщение и распознаёт текст через Google STT."""
    try:
        import speech_recognition as sr
        from pydub import AudioSegment

        file_path = tg_get_file_path(file_id)
        if not file_path:
            return None

        audio_bytes = tg_download_file(file_path)
        if not audio_bytes:
            return None

        with tempfile.TemporaryDirectory() as tmpdir:
            ogg_path = os.path.join(tmpdir, 'voice.ogg')
            wav_path = os.path.join(tmpdir, 'voice.wav')

            with open(ogg_path, 'wb') as f:
                f.write(audio_bytes)

            # Конвертируем ogg/opus → wav
            audio = AudioSegment.from_ogg(ogg_path)
            audio.export(wav_path, format='wav')

            # Распознаём через Google (бесплатно, без ключа)
            recognizer = sr.Recognizer()
            with sr.AudioFile(wav_path) as source:
                audio_data = recognizer.record(source)
            text = recognizer.recognize_google(audio_data, language='ru-RU')
            print(f"Голос распознан: {text}")
            return text

    except Exception as e:
        print(f"Ошибка распознавания голоса: {e}")
        return None


# ── GitHub ──────────────────────────────────────────────────

def gh_read(filepath):
    headers = {
        'Authorization': f'token {GITHUB_TOKEN}',
        'Accept': 'application/vnd.github.v3+json'
    }
    r = requests.get(
        f'https://api.github.com/repos/{GITHUB_REPO}/contents/{filepath}',
        headers=headers, timeout=15
    )
    if r.status_code == 200:
        d = r.json()
        return base64.b64decode(d['content']).decode('utf-8'), d['sha']
    return None, None

def gh_write(filepath, content, message, sha=None):
    headers = {
        'Authorization': f'token {GITHUB_TOKEN}',
        'Accept': 'application/vnd.github.v3+json'
    }
    if not sha:
        r = requests.get(
            f'https://api.github.com/repos/{GITHUB_REPO}/contents/{filepath}',
            headers=headers, timeout=15
        )
        sha = r.json().get('sha') if r.status_code == 200 else None

    payload = {
        'message': message,
        'content': base64.b64encode(content.encode('utf-8')).decode('utf-8')
    }
    if sha:
        payload['sha'] = sha

    r = requests.put(
        f'https://api.github.com/repos/{GITHUB_REPO}/contents/{filepath}',
        headers=headers, json=payload, timeout=30
    )
    return r.status_code in (200, 201)


# ── Claude API ──────────────────────────────────────────────

def claude(prompt, max_tokens=2000):
    """Вызывает Google Gemini API (бесплатно)."""
    if not GEMINI_KEY:
        print("GEMINI_API_KEY не задан")
        return None
    try:
        r = requests.post(
            f'https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_KEY}',
            headers={'content-type': 'application/json'},
            json={
                'contents': [{'parts': [{'text': prompt}]}],
                'generationConfig': {
                    'maxOutputTokens': max_tokens,
                    'temperature': 0.3
                }
            },
            timeout=120
        )
        data = r.json()
        return data['candidates'][0]['content']['parts'][0]['text']
    except Exception as e:
        print(f"Gemini ошибка: {e}")
        return None

def parse_json(text):
    try:
        clean = re.sub(r'```(?:json)?|```', '', text).strip()
        return json.loads(clean)
    except Exception as e:
        print(f"JSON parse error: {e}\nОтвет: {text[:300]}")
        return None


# ── Очередь найденных материалов ────────────────────────────

def queue_add(text, link, source):
    """Добавляет материал в очередь."""
    q = load_json(QUEUE_FILE, [])
    # Не добавляем дубликаты
    for item in q:
        if item.get('link') and item['link'] == link:
            return
        if get_hash(item.get('text','')) == get_hash(text):
            return
    q.append({
        'text': text,
        'link': link,
        'source': source,
        'added': datetime.now().isoformat()
    })
    save_json(QUEUE_FILE, q)
    print(f"  В очередь: {text[:60]}")

def queue_pop():
    """Берёт первый элемент из очереди."""
    q = load_json(QUEUE_FILE, [])
    if not q:
        return None
    item = q.pop(0)
    save_json(QUEUE_FILE, q)
    return item

def queue_len():
    return len(load_json(QUEUE_FILE, []))


# ── Анализ материала ────────────────────────────────────────

def analyze(text, link, source):
    """Claude проверяет: есть ли на сайте, куда добавить."""
    html, _ = gh_read(INDEX_FILE)
    if not html:
        return None

    soup = BeautifulSoup(html, 'html.parser')
    for tag in soup(['script', 'style']):
        tag.decompose()
    site_text = soup.get_text(separator=' ', strip=True)[:20000]

    resp = claude(f"""Ты помощник по управлению сайтом Александра Пронина (a-pronin.ru).

Структура сайта:
- Консалтинг (BITOBE): профиль, новости, мероприятия
- Дроны/FPV (ФГД СПб): профиль, новости, мероприятия

Текущий контент сайта:
{site_text}

Новый материал:
Источник: {source}
Текст: {text[:2000]}
Ссылка: {link or 'нет'}

Задача:
1. Проверь — есть ли уже этот материал на сайте?
2. Если нет — определи куда лучше добавить. Важный материал можно добавить в несколько мест.

Ответь СТРОГО в JSON (без markdown):
{{
  "found_on_site": true или false,
  "reason": "объяснение на русском (1-2 предложения)",
  "suggestion": "предложение для пользователя: куда и почему разместить",
  "placements": []
}}

Возможные значения placements:
"news-consulting", "news-drone", "events-consulting", "events-drone", "profile-consulting", "profile-drone"

Если found_on_site=true — placements пустой массив.""", max_tokens=1000)

    return parse_json(resp) if resp else None


# ── Генерация обновлённого HTML ─────────────────────────────

def generate_edit(text, link, date, placements, user_comment=''):
    """Claude вносит правки в index.html и возвращает обновлённый файл."""
    html, sha = gh_read(INDEX_FILE)
    if not html:
        return None, None

    extra = f"\nДополнительные инструкции: {user_comment}" if user_comment else ""
    places_str = ', '.join(PLACEMENT_LABELS.get(p, p) for p in placements)

    resp = claude(f"""Ты редактор HTML-сайта Александра Пронина (a-pronin.ru).

Добавь следующий материал в разделы: {places_str}
Текст материала: {text[:1500]}
Ссылка: {link or 'нет'}
Дата: {date}{extra}

Правила:
- Добавляй в начало соответствующего списка (самая свежая запись первой)
- Используй существующие CSS-классы сайта (news-item, event-item и т.д.)
- Не меняй ничего кроме нужных разделов
- Для новостей: добавляй в year-group с текущим годом; если года нет — создай его

Текущий index.html:
{html}

Верни ТОЛЬКО полный обновлённый HTML без пояснений и без markdown-блоков.""",
        max_tokens=32000
    )

    return resp, sha


# ── Pending ─────────────────────────────────────────────────

def pending_save(text, link, date, source, analysis, prev_sha):
    save_json(PENDING_FILE, {
        'text': text, 'link': link, 'date': date,
        'source': source, 'analysis': analysis,
        'prev_sha': prev_sha,
        'status': 'waiting_confirm',
        'created': datetime.now().isoformat()
    })

def pending_load():
    return load_json(PENDING_FILE, None)

def pending_clear():
    if os.path.exists(PENDING_FILE):
        os.remove(PENDING_FILE)


# ── Отправка одного предложения пользователю ────────────────

def propose_one(item):
    """Анализирует один элемент из очереди и отправляет предложение."""
    text   = item['text']
    link   = item['link']
    source = item['source']

    tg(f"🔍 <b>Анализирую...</b>\n<i>{text[:150]}</i>")

    _, prev_sha = gh_read(INDEX_FILE)
    result = analyze(text, link, source)

    if not result:
        tg("❌ Ошибка анализа. Пропускаю.")
        # Пробуем следующий из очереди в следующем запуске
        return

    if result.get('found_on_site'):
        tg(f"ℹ️ <b>Уже есть на сайте</b>\n{result.get('reason','')}")
        # Берём следующий из очереди
        next_item = queue_pop()
        if next_item:
            propose_one(next_item)
        return

    placements = result.get('placements', [])
    if not placements:
        tg(f"ℹ️ Клод: размещать не нужно\n{result.get('reason','')}")
        next_item = queue_pop()
        if next_item:
            propose_one(next_item)
        return

    date   = datetime.now().strftime('%d %b %Y')
    places = '\n'.join(f"• {PLACEMENT_LABELS.get(p,p)}" for p in placements)
    q_len  = queue_len()
    queue_note = f"\n\n📋 <i>Ещё в очереди: {q_len}</i>" if q_len > 0 else ""

    tg(
        f"🤖 <b>Клод предлагает</b>\n\n"
        f"📌 <b>Источник:</b> {source}\n"
        f"📝 <b>Материал:</b> {text[:300]}{'...' if len(text)>300 else ''}\n"
        + (f"🔗 <b>Ссылка:</b> {link}\n" if link else "") +
        f"\n💡 <b>Предложение:</b>\n{result.get('suggestion','')}\n\n"
        f"<b>Разместить в:</b>\n{places}\n\n"
        f"✅ <b>ДА</b>  |  ❌ <b>НЕТ</b>  |  ✏️ <i>или напиши/надиктуй правки</i>"
        + queue_note
    )
    pending_save(text, link, date, source, result, prev_sha)


# ── Подтверждение и запись ───────────────────────────────────

def do_confirm(pending, user_comment=''):
    tg("⚙️ <b>Вношу изменения на сайт...</b>")

    prev_html, _ = gh_read(INDEX_FILE)
    new_html, sha = generate_edit(
        pending['text'], pending['link'], pending['date'],
        pending['analysis']['placements'], user_comment
    )

    if not new_html or len(new_html) < 5000:
        tg("❌ Ошибка генерации HTML. Попробуй ещё раз.")
        return

    ok = gh_write(INDEX_FILE, new_html, f"AI: {pending['text'][:60]}", sha=sha)

    if ok:
        save_json(PENDING_FILE, {
            **pending,
            'status': 'done',
            'prev_html_b64': base64.b64encode(
                prev_html.encode('utf-8')).decode('utf-8') if prev_html else None
        })
        q_len = queue_len()
        queue_note = f"\n\n📋 Ещё в очереди: {q_len}" if q_len > 0 else ""
        tg(
            f"✅ <b>Сайт обновлён!</b>\n\n"
            f"🔗 {SITE_URL}\n\n"
            f"Если что-то не так — ответь <b>ОТКАТ</b>"
            + queue_note
        )
    else:
        tg("❌ Ошибка при записи в GitHub.")


def do_rollback(pending):
    b64 = pending.get('prev_html_b64')
    if not b64:
        tg("⚠️ Нет сохранённой версии для отката.")
        pending_clear()
        return

    tg("🔄 <b>Откатываю...</b>")
    prev_html = base64.b64decode(b64.encode('utf-8')).decode('utf-8')
    ok = gh_write(INDEX_FILE, prev_html, 'Rollback: отмена изменений AI')

    if ok:
        pending_clear()
        tg(f"✅ <b>Откат выполнен!</b>\n🔗 {SITE_URL}")
    else:
        tg("❌ Ошибка отката.")


# ── Обработка команд и сообщений ────────────────────────────

def process_commands():
    last_id = get_last_uid()
    updates = get_updates(offset=(last_id + 1) if last_id else None)

    for upd in updates:
        uid  = upd['update_id']
        save_last_uid(uid)

        msg     = upd.get('message', {})
        cid     = str(msg.get('chat', {}).get('id', ''))
        if cid != str(CHAT_ID):
            continue

        # Определяем текст: обычное сообщение или голосовое
        text = msg.get('text', '').strip()
        voice = msg.get('voice')

        if voice and not text:
            tg("🎤 <b>Получил голосовое, распознаю...</b>")
            text = transcribe_voice(voice['file_id'])
            if not text:
                tg("❌ Не удалось распознать голос. Попробуй написать текстом.")
                continue
            tg(f"📝 <b>Распознано:</b> <i>{text}</i>")

        if not text:
            continue

        print(f"← {text[:100]}")
        tl      = text.lower().strip()
        pending = pending_load()

        # ── Ответы на ожидающее подтверждение ──
        if pending and pending['status'] == 'waiting_confirm':
            if tl in ('да', 'yes', 'ok', 'ок', '+', 'давай', 'вноси', '✅'):
                do_confirm(pending)
            elif tl in ('нет', 'no', '-', 'пропустить', 'пропусти', '❌', 'skip'):
                pending_clear()
                tg("⏭ Пропущено.")
                # Берём следующий из очереди
                next_item = queue_pop()
                if next_item:
                    propose_one(next_item)
            elif tl in ('откат', 'rollback', 'отмена'):
                tg("⚠️ Изменения ещё не применялись — нечего откатывать.")
            else:
                # Уточнение от пользователя (текст или голос)
                do_confirm(pending, user_comment=text)
            continue

        # ── Откат после успешного применения ──
        if pending and pending['status'] == 'done':
            if tl in ('откат', 'rollback', 'отмена', 'отменить'):
                do_rollback(pending)
                continue
            else:
                pending_clear()
                # Берём следующий из очереди
                next_item = queue_pop()
                if next_item:
                    propose_one(next_item)

        # ── Команды ──
        if text.startswith('/help') or text.startswith('/start'):
            tg(
                '🤖 <b>Pronin Monitor + Claude AI</b>\n\n'
                '🔍 Бот мониторит сайты и ВКонтакте.\n'
                'Клод анализирует каждый материал и предлагает размещение.\n\n'
                '<b>Ручной режим:</b>\n'
                'Отправь текст или 🎤 <b>голосовое</b> → Клод проверит и предложит.\n\n'
                '<b>Ответы на предложения:</b>\n'
                '✅ ДА — внести изменения\n'
                '❌ НЕТ — пропустить, взять следующее\n'
                '🔄 ОТКАТ — отменить последнее изменение\n'
                '✏️🎤 <i>текст/голос</i> — уточнить куда/как разместить\n\n'
                '/status — очередь и текущее состояние\n'
                '/help — эта справка'
            )

        elif text.startswith('/status'):
            p     = pending_load()
            q_len = queue_len()
            lines = []
            if p:
                status_label = 'ожидает подтверждения' if p['status'] == 'waiting_confirm' else 'применено (можно откатить)'
                lines.append(f"⏳ <b>Текущее:</b> {status_label}\n{p.get('text','')[:150]}")
            else:
                lines.append("✅ Нет текущего действия")
            if q_len:
                lines.append(f"📋 В очереди: {q_len} материалов")
            tg('\n\n'.join(lines))

        else:
            # Свободный текст / голос → новый материал
            urls  = re.findall(r'https?://\S+', text)
            link  = urls[0] if urls else ''
            clean = text.replace(link, '').strip() if link else text

            if len(clean) > 15:
                # Если сейчас нет pending — анализируем сразу
                # Иначе добавляем в очередь
                if pending_load() is None:
                    propose_one({'text': clean, 'link': link, 'source': 'Вручную'})
                else:
                    queue_add(clean, link, 'Вручную')
                    tg(f"📋 Добавлено в очередь. Сначала ответь на текущее предложение.")
            else:
                tg("⚠️ Слишком короткий текст. Отправь описание (и ссылку если есть).")


# ── Мониторинг ──────────────────────────────────────────────

def get_vk_data(screen_name):
    try:
        r = requests.get('https://api.vk.com/method/utils.resolveScreenName', params={
            'screen_name': screen_name, 'access_token': VK_TOKEN, 'v': '5.131'
        }, timeout=10)
        d = r.json()
        if 'error' in d or not d.get('response'):
            return None
        obj = d['response']
        oid = obj['object_id']
        if obj['type'] in ('group', 'page'):
            oid = -oid

        r2 = requests.get('https://api.vk.com/method/wall.get', params={
            'owner_id': oid, 'count': 50, 'filter': 'all',
            'access_token': VK_TOKEN, 'v': '5.131'
        }, timeout=10)
        wall = r2.json()
        if 'error' in wall:
            return None
        posts = wall.get('response', {}).get('items', [])
        return [
            {'text': p['text'], 'link': f"https://vk.com/wall{oid}_{p['id']}"}
            for p in posts if p.get('text', '').strip()
        ], oid
    except Exception as e:
        print(f"ВК ошибка {screen_name}: {e}")
        return None


def process_web(page, state):
    url  = page['url']
    html = fetch_page(url)
    if not html:
        return
    text = extract_text(html)
    h    = get_hash(text)

    found = [
        (kw, get_snippet(text, kw), url)
        for kw in page['keywords'] if get_snippet(text, kw)
    ]

    if url not in state:
        state[url] = {'hash': h, 'checked': datetime.now().isoformat()}
        if found:
            kw, snippet, link = found[0]
            queue_add(snippet, link, page['section'])
    elif h != state[url].get('hash'):
        state[url]['hash']    = h
        state[url]['checked'] = datetime.now().isoformat()
        if found:
            kw, snippet, link = found[0]
            queue_add(snippet, link, page['section'])
    else:
        state[url]['checked'] = datetime.now().isoformat()
        print(f"  ок: {url}")


def process_vk(page, state):
    name   = page['screen_name']
    key    = f"vk_{name}"
    result = get_vk_data(name)
    if not result:
        return
    posts, oid = result
    if not posts:
        return

    h = get_hash('\n'.join(p['text'] for p in posts))

    found = []
    for post in posts:
        for kw in page['keywords']:
            if kw.lower() in post['text'].lower():
                found.append(post)
                break

    prev_links = set(state.get(key, {}).get('seen_links', []))

    if key not in state:
        state[key] = {
            'hash': h,
            'checked': datetime.now().isoformat(),
            'seen_links': [p['link'] for p in found[:50]]
        }
        if found:
            # При первом сканировании добавляем только первый пост
            queue_add(found[0]['text'], found[0]['link'],
                      f"ВК vk.com/{name} ({page['section']})")

    elif h != state[key].get('hash'):
        state[key]['hash']    = h
        state[key]['checked'] = datetime.now().isoformat()
        # Только реально новые посты — каждый отдельно в очередь
        new_posts = [p for p in found if p['link'] not in prev_links]
        for post in new_posts:
            queue_add(post['text'], post['link'],
                      f"ВК vk.com/{name} ({page['section']})")
        state[key]['seen_links'] = [p['link'] for p in found[:50]]
    else:
        state[key]['checked'] = datetime.now().isoformat()
        print(f"  ок: vk.com/{name}")


# ── Главная ─────────────────────────────────────────────────

def run():
    print(f"=== Запуск {datetime.now().strftime('%d.%m.%Y %H:%M')} ===")

    # 1. Обрабатываем сообщения пользователя
    process_commands()

    # 2. Мониторинг — добавляет новые материалы в очередь
    pending = pending_load()
    if pending and pending.get('status') in ('waiting_confirm', 'done'):
        print("Есть pending — мониторинг пропускаю")
    else:
        state = load_json(STATE_FILE, {})

        for page in WEB_PAGES:
            print(f"Веб: {page['url']}")
            process_web(page, state)

        if VK_TOKEN:
            for page in VK_PAGES:
                print(f"ВК: vk.com/{page['screen_name']}")
                process_vk(page, state)
        else:
            print("VK_TOKEN не задан")

        save_json(STATE_FILE, state)

        # 3. Если pending нет — берём первый из очереди
        if pending_load() is None:
            item = queue_pop()
            if item:
                propose_one(item)

    print("=== Готово ===")


if __name__ == '__main__':
    run()
