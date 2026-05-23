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
QUEUE_FILE        = 'data/queue.json'
MANUAL_QUEUE_FILE = 'data/manual_queue.json'
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

def tg(message, reply_markup=None):
    try:
        data = {'chat_id': CHAT_ID, 'text': message,
                'parse_mode': 'HTML', 'disable_web_page_preview': True}
        if reply_markup:
            data['reply_markup'] = json.dumps(reply_markup)
        requests.post(
            f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage',
            data=data, timeout=10
        )
    except Exception as e:
        print(f"TG ошибка: {e}")

def tg_answer_callback(callback_query_id):
    try:
        requests.post(
            f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/answerCallbackQuery',
            data={'callback_query_id': callback_query_id},
            timeout=10
        )
    except: pass

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
        params = {'timeout': 0, 'allowed_updates': ['message', 'callback_query']}
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
    """Читает файл из GitHub — сначала SHA через API, контент через raw URL."""
    headers = {
        'Authorization': f'token {GITHUB_TOKEN}',
        'Accept': 'application/vnd.github.v3+json'
    }
    # Получаем SHA через API
    r = requests.get(
        f'https://api.github.com/repos/{GITHUB_REPO}/contents/{filepath}',
        headers=headers, timeout=15
    )
    if r.status_code != 200:
        print(f"gh_read API ошибка {r.status_code}: {r.text[:200]}")
        return None, None
    sha = r.json().get('sha')

    # Читаем содержимое через raw URL — файл публичный, токен не нужен
    raw_url = f'https://raw.githubusercontent.com/{GITHUB_REPO}/main/{filepath}'
    r2 = requests.get(raw_url, timeout=30)
    if r2.status_code != 200:
        print(f"gh_read raw ошибка {r2.status_code}")
        return None, None

    content = r2.text
    print(f"gh_read: получено {len(content)} символов")
    return content, sha

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
        print(f"Gemini: отправляю запрос ({len(prompt)} символов)...")
        r = requests.post(
            f'https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-lite:generateContent?key={GEMINI_KEY}',
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
        print(f"Gemini статус: {r.status_code}")

        # Ошибка API
        if 'error' in data:
            print(f"Gemini API ошибка: {data['error']}")
            return None

        # Safety block или пустой ответ
        candidates = data.get('candidates', [])
        if not candidates:
            print(f"Gemini: нет кандидатов. Ответ: {data}")
            return None

        candidate = candidates[0]
        finish_reason = candidate.get('finishReason', '')
        if finish_reason == 'SAFETY':
            print(f"Gemini: заблокировано safety filters")
            return None

        parts = candidate.get('content', {}).get('parts', [])
        if not parts:
            print(f"Gemini: пустой content. Кандидат: {candidate}")
            return None

        return parts[0]['text']

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

ANALYZED_FILE = 'data/analyzed.json'
NEWS_FILE     = 'data/news.json'
EVENTS_FILE   = 'data/events.json'

def manual_queue_add(text, link):
    """Добавляет материал от пользователя в приоритетную очередь."""
    q = load_json(MANUAL_QUEUE_FILE, [])
    q.append({
        'text': text, 'link': link,
        'source': 'Вручную от пользователя',
        'added': datetime.now().isoformat()
    })
    save_json(MANUAL_QUEUE_FILE, q)
    print(f"manual_queue: добавлено — {text[:60]}")

def manual_queue_pop():
    q = load_json(MANUAL_QUEUE_FILE, [])
    if not q:
        return None
    item = q.pop(0)
    save_json(MANUAL_QUEUE_FILE, q)
    return item

def manual_queue_len():
    return len(load_json(MANUAL_QUEUE_FILE, []))


def fetch_url_content(url):
    """Читает содержимое страницы по ссылке."""
    try:
        r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=15)
        r.encoding = 'utf-8'
        soup = BeautifulSoup(r.text, 'html.parser')
        for tag in soup(['script', 'style', 'nav', 'header', 'footer']):
            tag.decompose()
        text = soup.get_text(separator=' ', strip=True)
        return ' '.join(text.split())[:3000]
    except Exception as e:
        print(f"fetch_url_content ошибка: {e}")
        return None


def get_existing_content():
    """Читает news.json и events.json — только заголовки для сравнения."""
    news_raw,   _ = gh_read(NEWS_FILE)
    events_raw, _ = gh_read(EVENTS_FILE)

    lines = ["Текущие новости и мероприятия на сайте:"]

    if news_raw:
        try:
            news = json.loads(news_raw)
            for section, items in news.items():
                for item in items:
                    lines.append(f"[Новость/{section}] {item.get('date','')} — {item.get('title','')}")
        except: pass

    if events_raw:
        try:
            events = json.loads(events_raw)
            for section, types in events.items():
                for etype, items in types.items():
                    for item in items:
                        lines.append(f"[Мероприятие/{section}/{etype}] {item.get('date','')} — {item.get('title','')}")
        except: pass

    return "\n".join(lines)

def analyze_batch(items):
    """Анализирует пакет материалов одним запросом к Gemini.
    
    items: список {'text', 'link', 'source'}
    Возвращает список результатов или None при ошибке (429 и т.д.)
    """
    print(f"analyze_batch: анализирую {len(items)} материалов...")
    site_text = get_existing_content()

    items_str = ''
    for i, item in enumerate(items):
        # Если есть ссылка — читаем содержимое страницы
        url_content = ''
        if item.get('link'):
            print(f"analyze_batch: читаю {item['link']}")
            url_content = fetch_url_content(item['link'])

        items_str += f"""
Материал {i+1}:
  Источник: {item['source']}
  Текст от пользователя: {item['text'][:300]}
  Ссылка: {item.get('link') or 'нет'}
  Содержимое по ссылке: {url_content[:1000] if url_content else 'не удалось загрузить'}
"""

    resp = claude(f"""Ты помощник по управлению сайтом Александра Пронина (a-pronin.ru).

Структура сайта:
- Консалтинг (BITOBE): профиль, новости, мероприятия  
- Дроны/FPV (ФГД СПб): профиль, новости, мероприятия

Текущий контент сайта:
{site_text}

Проанализируй каждый материал: есть ли он уже на сайте, куда добавить.

{items_str}

Ответь СТРОГО в JSON-массиве (без markdown), по одному объекту на каждый материал:
[
  {{
    "index": 1,
    "found_on_site": true или false,
    "reason": "объяснение 1-2 предложения",
    "suggestion": "куда и почему разместить",
    "placements": [],
    "title": "краткий заголовок материала (из содержимого ссылки если есть)",
    "description": "краткое описание 1-2 предложения (из содержимого ссылки если есть)",
    "event_day": "день числом или диапазон типа 28-29, если это мероприятие",
    "event_month": "месяц сокращённо на русском (янв/фев/мар/апр/май/июн/июл/авг/сен/окт/ноя/дек)",
    "event_year": "год четырьмя цифрами"
  }}
]

Возможные placements: "news-consulting", "news-drone", "events-consulting", "events-drone"
ВАЖНО: НЕ предлагай изменения в профиль — профиль редактируется вручную.
Если found_on_site=true — placements пустой массив.
Для title и description используй реальное содержимое материала, НЕ текст команды пользователя.""", max_tokens=2000)

    if not resp:
        # None означает ошибку API (429 или другую) — не теряем материалы
        print("analyze_batch: Gemini не ответил — материалы остаются в очереди")
        return None

    results = parse_json(resp)
    if not results or not isinstance(results, list):
        print(f"analyze_batch: неверный формат ответа: {resp[:200]}")
        return None

    print(f"analyze_batch: получено {len(results)} результатов")
    return results

def analyze(text, link, source):
    """Обёртка для одиночного анализа (используется при ручном вводе)."""
    results = analyze_batch([{'text': text, 'link': link, 'source': source}])
    if results is None:
        return None
    return results[0] if results else None


# ── Генерация обновлённого HTML ─────────────────────────────

def apply_json_edit(text, link, date, placements, user_comment='', analysis=None):
    if analysis is None:
        analysis = {}
    """Добавляет материал в news.json или events.json — без редактирования HTML."""
    success = True

    for placement in placements:
        if placement.startswith('news-'):
            section = placement.replace('news-', '')

            # Читаем свежий SHA через API (не через raw)
            headers = {'Authorization': f'token {GITHUB_TOKEN}',
                      'Accept': 'application/vnd.github.v3+json'}
            r = requests.get(
                f'https://api.github.com/repos/{GITHUB_REPO}/contents/{NEWS_FILE}',
                headers=headers, timeout=15)
            print(f"apply_json_edit news API статус: {r.status_code}")
            if r.status_code != 200:
                print(f"apply_json_edit ошибка: {r.text[:200]}")
                success = False
                continue
            sha = r.json().get('sha')
            news_raw = base64.b64decode(
                r.json()['content'].replace('\n','').replace(' ','')).decode('utf-8')
            news = json.loads(news_raw) if news_raw else {'consulting': [], 'drone': []}

            if section not in news:
                news[section] = []
            new_item = {
                'id': int(datetime.now().timestamp()),
                'section': section,
                'source': 'BITOBE' if 'bitobe' in (link or '').lower() or 'bitobe' in text.lower() else ('ВКонтакте' if 'vk.' in (link or '') else 'РБК' if 'rbc.' in (link or '') else ''),
                'date': analysis.get('event_day','') + ' ' + analysis.get('event_month','') + ' ' + analysis.get('event_year','') if analysis.get('event_day') else date,
                'title': analysis.get('title') or text[:100],
                'text': analysis.get('description') or text[:300],
                'link': link or ''
            }
            news[section].insert(0, new_item)
            ok = gh_write(NEWS_FILE, json.dumps(news, ensure_ascii=False, indent=2),
                         f"Новость: {text[:50]}", sha=sha)
            print(f"apply_json_edit news write: {ok}")
            if not ok: success = False

        elif placement.startswith('events-'):
            section = placement.replace('events-', '')

            # Читаем свежий SHA через API
            headers = {'Authorization': f'token {GITHUB_TOKEN}',
                      'Accept': 'application/vnd.github.v3+json'}
            r = requests.get(
                f'https://api.github.com/repos/{GITHUB_REPO}/contents/{EVENTS_FILE}',
                headers=headers, timeout=15)
            print(f"apply_json_edit events API статус: {r.status_code}")
            if r.status_code != 200:
                print(f"apply_json_edit ошибка: {r.text[:200]}")
                success = False
                continue
            sha = r.json().get('sha')
            events_raw = base64.b64decode(
                r.json()['content'].replace('\n','').replace(' ','')).decode('utf-8')
            events = json.loads(events_raw) if events_raw else {
                'consulting': {'upcoming': [], 'past': []},
                'drone': {'upcoming': [], 'past': []}
            }

            if section not in events:
                events[section] = {'upcoming': [], 'past': []}
            etype = 'upcoming'
            new_item = {
                'id': int(datetime.now().timestamp()),
                'section': section,
                'type': etype,
                'day':   analysis.get('event_day', ''),
                'month': analysis.get('event_month', ''),
                'year':  analysis.get('event_year', str(datetime.now().year)),
                'title': analysis.get('title') or text[:100],
                'text':  analysis.get('description') or text[:300],
                'link':  link or '',
                'link_text': 'Подробнее'
            }
            events[section][etype].insert(0, new_item)
            ok = gh_write(EVENTS_FILE, json.dumps(events, ensure_ascii=False, indent=2),
                         f"Мероприятие: {text[:50]}", sha=sha)
            print(f"apply_json_edit events write: {ok}")
            if not ok: success = False

    return success


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
    tg("⚙️ <b>Добавляю в базу сайта...</b>")

    # Если пользователь уточнил placement в комментарии — переопределяем
    placements = pending['analysis'].get('placements', [])
    if user_comment:
        comment_lower = user_comment.lower()
        new_placements = []
        if 'консалтинг' in comment_lower:
            if 'мероприят' in comment_lower: new_placements.append('events-consulting')
            elif 'новост' in comment_lower:  new_placements.append('news-consulting')
            else: new_placements = ['events-consulting' if any('events' in p for p in placements) else 'news-consulting']
        if 'дрон' in comment_lower or 'фгд' in comment_lower:
            if 'мероприят' in comment_lower: new_placements.append('events-drone')
            elif 'новост' in comment_lower:  new_placements.append('news-drone')
            else: new_placements = ['events-drone' if any('events' in p for p in placements) else 'news-drone']
        if new_placements:
            placements = new_placements
            pending['analysis']['placements'] = placements
            print(f"do_confirm: placement переопределён на {placements}")

    ok = apply_json_edit(
        pending['text'], pending['link'], pending['date'],
        placements, user_comment,
        analysis=pending['analysis']
    )

    if ok:
        save_json(PENDING_FILE, {**pending, 'status': 'done'})
        analyzed = load_json(ANALYZED_FILE, [])
        q_len = queue_len() + len(analyzed)
        queue_note = f"\n\n📋 Ещё в очереди: {q_len}" if q_len > 0 else ""
        rollback_btn = {'inline_keyboard': [[
            {'text': '✅ ПРИНЯТО', 'callback_data': 'confirm_done'},
            {'text': '🔄 ОТКАТ',   'callback_data': 'confirm_rollback'}
        ]]}
        tg(
            f"✅ <b>Готово! Сайт обновлён.</b>\n\n"
            f"🔗 {SITE_URL}"
            + queue_note,
            reply_markup=rollback_btn
        )
        # После подтверждения — сначала проверяем приоритетную очередь
        manual_item = manual_queue_pop()
        if manual_item:
            propose_one(manual_item)
        elif analyzed:
            next_item = analyzed.pop(0)
            save_json(ANALYZED_FILE, analyzed)
            propose_one_analyzed(next_item)
    else:
        tg("❌ Ошибка при записи в GitHub.")


def do_rollback(pending):
    """Откатывает последнее добавление — удаляет первый элемент из нужного JSON."""
    tg("🔄 <b>Откатываю...</b>")
    placements = pending.get('analysis', {}).get('placements', [])
    success = True

    for placement in placements:
        if placement.startswith('news-'):
            section = placement.replace('news-', '')
            news_raw, sha = gh_read(NEWS_FILE)
            if news_raw:
                news = json.loads(news_raw)
                if news.get(section):
                    news[section].pop(0)  # Удаляем первый (последний добавленный)
                    ok = gh_write(NEWS_FILE, json.dumps(news, ensure_ascii=False, indent=2),
                                 'Rollback: отмена новости', sha=sha)
                    if not ok: success = False

        elif placement.startswith('events-'):
            section = placement.replace('events-', '')
            events_raw, sha = gh_read(EVENTS_FILE)
            if events_raw:
                events = json.loads(events_raw)
                if events.get(section, {}).get('upcoming'):
                    events[section]['upcoming'].pop(0)
                    ok = gh_write(EVENTS_FILE, json.dumps(events, ensure_ascii=False, indent=2),
                                 'Rollback: отмена мероприятия', sha=sha)
                    if not ok: success = False

    if success:
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

        # Обработка нажатий на inline-кнопки
        cb = upd.get('callback_query')
        if cb:
            cb_cid = str(cb.get('from', {}).get('id', ''))
            if cb_cid == str(CHAT_ID):
                tg_answer_callback(cb['id'])
                cb_data = cb.get('data', '')
                pending = pending_load()
                if cb_data == 'confirm_yes' and pending and pending['status'] == 'waiting_confirm':
                    do_confirm(pending)
                elif cb_data == 'confirm_no' and pending and pending['status'] == 'waiting_confirm':
                    pending_clear()
                    tg("⏭ Пропущено.")
                    # Сначала приоритетная очередь
                    manual_item = manual_queue_pop()
                    if manual_item:
                        propose_one(manual_item)
                    else:
                        analyzed = load_json(ANALYZED_FILE, [])
                        if analyzed:
                            first = analyzed.pop(0)
                            save_json(ANALYZED_FILE, analyzed)
                            propose_one_analyzed(first)
                elif cb_data == 'confirm_done' and pending and pending['status'] == 'done':
                    pending_clear()
                    tg("👍 Принято.")
                    manual_item = manual_queue_pop()
                    if manual_item:
                        propose_one(manual_item)
                    else:
                        analyzed = load_json(ANALYZED_FILE, [])
                        if analyzed:
                            first = analyzed.pop(0)
                            save_json(ANALYZED_FILE, analyzed)
                            propose_one_analyzed(first)
                elif cb_data == 'confirm_rollback' and pending and pending['status'] == 'done':
                    do_rollback(pending)
            continue

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
                tg("✅ Принято.")
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

            if link:
                # Убираем команды типа "сделай новость", "добавь" из текста
                import re as _re
                clean_text = _re.sub(
                    r'^(добавь|сделай|размести|поставь|внеси|создай)[^,\.]*[,\.]?\s*',
                    '', clean, flags=_re.IGNORECASE
                ).strip()
                if len(clean_text) < 5:
                    clean_text = clean
                manual_queue_add(clean_text, link)
                if pending_load() is None:
                    item = manual_queue_pop()
                    if item:
                        propose_one(item)
                else:
                    tg(f"📋 Твой материал в приоритете. Сначала ответь на текущее предложение — потом сразу перейдём к твоему.")
            else:
                tg("⚠️ Отправь ссылку на материал.")


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


# ── Пакетная обработка очереди ──────────────────────────────

def process_queue_batch():
    """Анализирует всю очередь одним запросом, сохраняет результаты,
    показывает первый материал пользователю."""

    # Сначала проверяем приоритетную очередь (от пользователя)
    manual_item = manual_queue_pop()
    if manual_item:
        print(f"process_queue_batch: приоритет — материал от пользователя")
        propose_one(manual_item)
        return

    # Проверяем — есть ли уже проанализированные материалы
    analyzed = load_json(ANALYZED_FILE, [])
    if analyzed:
        # Уже есть готовые — показываем первый
        print(f"process_queue_batch: есть {len(analyzed)} проанализированных, показываю первый")
        item = analyzed.pop(0)
        save_json(ANALYZED_FILE, analyzed)
        if item.get('skip'):
            # Уже на сайте — берём следующий
            process_queue_batch()
            return
        propose_one_analyzed(item)
        return

    # Берём всё из очереди
    queue = load_json(QUEUE_FILE, [])
    if not queue:
        print("process_queue_batch: очередь пуста")
        return

    print(f"process_queue_batch: в очереди {len(queue)} материалов")

    # Анализируем пакетом (максимум 5 за раз)
    batch = queue[:5]
    rest  = queue[5:]

    results = analyze_batch(batch)

    if results is None:
        # Gemini недоступен (429) — оставляем очередь как есть, попробуем в следующий раз
        print("process_queue_batch: Gemini недоступен, очередь сохранена для следующего запуска")
        tg("⏳ Gemini временно недоступен (лимит запросов). Повторю в следующем запуске.")
        return

    # Сохраняем остаток очереди
    save_json(QUEUE_FILE, rest)

    # Формируем список проанализированных
    analyzed_items = []
    for i, result in enumerate(results):
        item = batch[i] if i < len(batch) else {}
        if result.get('found_on_site'):
            analyzed_items.append({**item, **result, 'skip': True})
        elif result.get('placements'):
            analyzed_items.append({**item, **result, 'skip': False,
                                    'date': datetime.now().strftime('%d %b %Y')})

    if not analyzed_items:
        print("process_queue_batch: все материалы уже на сайте")
        # Если ещё есть в очереди — обрабатываем следующий пакет
        if rest:
            process_queue_batch()
        return

    # Показываем первый, остальные сохраняем
    first = analyzed_items[0]
    save_json(ANALYZED_FILE, analyzed_items[1:])

    if first.get('skip'):
        process_queue_batch()
    else:
        propose_one_analyzed(first)


def propose_one_analyzed(item):
    """Отправляет пользователю предложение по уже проанализированному материалу."""
    text      = item.get('text', '')
    link      = item.get('link', '')
    source    = item.get('source', '')
    placements = item.get('placements', [])
    analyzed  = load_json(ANALYZED_FILE, [])
    q_len     = queue_len() + len(analyzed)

    places = '\n'.join(f"• {PLACEMENT_LABELS.get(p,p)}" for p in placements)
    queue_note = f"\n\n📋 <i>Ещё в очереди: {q_len}</i>" if q_len > 0 else ""

    buttons = {
        'inline_keyboard': [[
            {'text': '✅ ДА', 'callback_data': 'confirm_yes'},
            {'text': '❌ НЕТ', 'callback_data': 'confirm_no'},
        ]]
    }
    # Формируем превью заголовка и подстрочника из анализа
    preview_title = item.get('title', '')
    preview_desc  = item.get('description', '')
    preview_block = ''
    if preview_title:
        preview_block = f"\n\n📋 <b>Как будет выглядеть:</b>\n<b>{preview_title}</b>"
        if preview_desc:
            preview_block += f"\n<i>{preview_desc}</i>"

    tg(
        f"🤖 <b>Клод предлагает</b>\n\n"
        f"📌 <b>Источник:</b> {source}\n"
        + (f"🔗 <b>Ссылка:</b> {link}\n" if link else "") +
        f"\n💡 <b>Предложение:</b>\n{item.get('suggestion','')}\n\n"
        f"<b>Разместить в:</b>\n{places}"
        + preview_block
        + queue_note,
        reply_markup=buttons
    )

    _, prev_sha = gh_read(INDEX_FILE)
    pending_save(text, link, item.get('date', datetime.now().strftime('%d %b %Y')),
                 source, item, prev_sha)
    print(f"propose_one_analyzed: ожидаю подтверждения от пользователя")


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

        # 3. Пакетный анализ очереди и показ первого результата
        # Только если нет ожидающего подтверждения
        if pending_load() is None:
            process_queue_batch()

    print("=== Готово ===")


if __name__ == '__main__':
    run()
