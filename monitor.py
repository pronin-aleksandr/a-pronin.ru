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
    {'url': 'https://bitobe.ru/team/4573/',                              'section': 'Консалтинг', 'keywords': ['Пронин']},
    {'url': 'https://fgd.spb.ru/',                                       'section': 'Дроны',      'keywords': ['Пронин']},
    {'url': 'https://companies.rbc.ru/experts/25902/aleksandr-pronin/', 'section': 'Консалтинг', 'keywords': ['Пронин']},
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


def fetch_vk_post(url):
    """Получает текст поста ВКонтакте через VK API."""
    try:
        # Извлекаем owner_id и post_id из ссылки
        # Форматы: vk.com/wall-200393_2308, vk.ru/wall-200393_2308
        import re
        match = re.search(r'wall(-?\d+)_(\d+)', url)
        if not match:
            return None
        owner_id = match.group(1)
        post_id  = match.group(2)

        r = requests.get('https://api.vk.com/method/wall.getById', params={
            'posts':        f"{owner_id}_{post_id}",
            'access_token': VK_TOKEN,
            'v':            '5.131'
        }, timeout=10)
        data = r.json()
        if 'error' in data:
            print(f"VK API ошибка: {data['error']}")
            return None
        response = data.get('response', {})
        if isinstance(response, dict):
            items = response.get('items', [])
        elif isinstance(response, list):
            items = response
        else:
            items = []
        if items:
            text = items[0].get('text', '') if isinstance(items[0], dict) else ''

            print(f"fetch_vk_post: получено {len(text)} символов")
            return text[:3000] if text else None
        return None
    except Exception as e:
        print(f"fetch_vk_post ошибка: {e}")
        return None

def fetch_url_content(url):
    """Читает содержимое страницы по ссылке."""
    # Для ВКонтакте используем API
    if 'vk.com' in url or 'vk.ru' in url:
        if 'wall' in url:
            vk_text = fetch_vk_post(url)
            if vk_text:
                return vk_text

    try:
        r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=15)
        r.encoding = 'utf-8'
        soup = BeautifulSoup(r.text, 'html.parser')
        for tag in soup(['script', 'style', 'nav', 'header', 'footer']):
            tag.decompose()
        text = soup.get_text(separator=' ', strip=True)
        result = ' '.join(text.split())[:3000]
        print(f"fetch_url_content: получено {len(result)} символов")
        return result
    except Exception as e:
        print(f"fetch_url_content ошибка: {e}")
        return None


def normalize_date(date_str):
    """Переводит английские месяцы в русские."""
    en_to_ru = {
        'january':'янв','february':'фев','march':'мар','april':'апр',
        'may':'май','june':'июн','july':'июл','august':'авг',
        'september':'сен','october':'окт','november':'ноя','december':'дек',
        'jan':'янв','feb':'фев','mar':'мар','apr':'апр',
        'jun':'июн','jul':'июл','aug':'авг','sep':'сен',
        'oct':'окт','nov':'ноя','dec':'дек'
    }
    result = date_str
    for en, ru in en_to_ru.items():
        result = result.lower().replace(en, ru)
    return result


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

    # Маппинг VK owner_id → раздел сайта
    VK_SECTION_MAP = {
        '-200393': 'consulting',  # vk.com/bitobe
        '-41670789': 'drone',     # vk.com/fgdrus
        '-145367898': 'drone',    # vk.com/fgdpskov
        '-222511091': 'drone',    # vk.com/fgdspb
    }

    items_str = ''
    for i, item in enumerate(items):
        # Если есть ссылка — читаем содержимое страницы
        url_content = ''
        section_hint = ''
        if item.get('link'):
            print(f"analyze_batch: читаю {item['link']}")
            url_content = fetch_url_content(item['link'])

            # Определяем раздел по owner_id из VK-ссылки
            import re as _re2
            m = _re2.search(r'wall(-?\d+)_', item['link'])
            if m:
                owner = m.group(1)
                section = VK_SECTION_MAP.get(owner)
                if section:
                    section_hint = f"  ВАЖНО: этот пост из ВКонтакте принадлежит группе {owner} — это раздел {'консалтинг' if section == 'consulting' else 'дроны'} сайта. Размещай только в {'news-consulting или events-consulting' if section == 'consulting' else 'news-drone или events-drone'}."

        items_str += f"""
Материал {i+1}:
  Источник: {item['source']}
  Текст от пользователя: {item['text'][:300]}
  Ссылка: {item.get('link') or 'нет'}
  Содержимое по ссылке: {url_content[:1000] if url_content else 'не удалось загрузить'}
{section_hint}
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
    "title": "ОБЯЗАТЕЛЬНО: краткий заголовок материала на русском (из содержимого ссылки, не из команды пользователя)",
    "description": "ОБЯЗАТЕЛЬНО: описание 1-2 предложения на русском (из содержимого ссылки)",
    "news_date": "дата публикации новости из содержимого статьи в формате ДД месяц ГГГГ на русском, например: 31 мая 2026. Если дата не найдена — пустая строка.",
    "event_day": "день числом или диапазон типа 28-29 (только для мероприятий)",
    "event_month": "месяц сокращённо на русском: янв/фев/мар/апр/май/июн/июл/авг/сен/окт/ноя/дек (только для мероприятий)",
    "event_year": "год четырьмя цифрами (только для мероприятий)"
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
    for r in results:
        print(f"  title={r.get('title','—')[:50]} desc={r.get('description','—')[:50]}")
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
            # Проверка дублей по ссылке
            if link and any(e.get('link') == link for e in news[section]):
                print(f"apply_json_edit: ссылка уже есть в {section}, пропускаю")
                continue
            new_item = {
                'id': int(datetime.now().timestamp()),
                'section': section,
                'source': ('BITOBE' if 'bitobe' in (link or '').lower() or 'bitobe' in text.lower()
                          else 'ВКонтакте' if 'vk.' in (link or '')
                          else 'РБК' if 'rbc.' in (link or '')
                          else 'Telegram' if 't.me' in (link or '')
                          else ''),
                'date': normalize_date(analysis.get('news_date','')) if analysis.get('news_date') else (normalize_date(analysis.get('event_day','') + ' ' + analysis.get('event_month','') + ' ' + analysis.get('event_year','')) if analysis.get('event_day') else normalize_date(date)),
                'title': analysis.get('title') or text[:100],
                'text': analysis.get('description') or text[:300],
                'link': link or ''
            }
            news[section].insert(0, new_item)
            ok = gh_write(NEWS_FILE, json.dumps(news, ensure_ascii=False, indent=2),
                         f"Новость: {text[:50]}", sha=sha)
            print(f"apply_json_edit news write: {ok} | section={section} | title={new_item.get('title','')[:40]}")
            if not ok:
                success = False
                print(f"apply_json_edit: ОШИБКА записи news/{section}")

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
            # Проверка дублей по ссылке
            if link and any(e.get('link') == link for etype_list in events[section].values() for e in etype_list):
                print(f"apply_json_edit: ссылка уже есть в events/{section}, пропускаю")
                continue
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

def pending_save(text, link, date, source, analysis, prev_sha, status='waiting_confirm'):
    save_json(PENDING_FILE, {
        'text': text, 'link': link, 'date': date,
        'source': source, 'analysis': analysis,
        'prev_sha': prev_sha,
        'status': status,
        'created': datetime.now().isoformat()
    })

def pending_load():
    data = load_json(PENDING_FILE, None)
    if not data:  # None или пустой dict {}
        return None
    return data

def pending_clear():
    if os.path.exists(PENDING_FILE):
        os.remove(PENDING_FILE)


# ── Единый флоу предложения ──────────────────────────────────
def do_confirm(pending, user_comment=''):
    if user_comment and not pending.get('date_confirmed'):
        tg("✏️ <b>Переделываю через AI...</b>")
        analysis = pending.get('analysis', {})
        link = pending.get('link', '')
        article_text = ''
        if link:
            c = fetch_url_content(link)
            if c:
                article_text = c[:2000]
        rework_prompt = (
            f'Измени карточку материала по инструкции пользователя.\n'
            f'Заголовок: {analysis.get("title","")}\n'
            f'Описание: {analysis.get("description","")}\n'
            f'Инструкция: {user_comment}\n'
            + (f'Содержимое статьи:\n{article_text}\n' if article_text else '') +
            'Ответь СТРОГО в JSON без markdown: {"title": "...", "description": "...", "suggestion": "..."}'
        )
        result = claude(rework_prompt, max_tokens=500)
        print(f"rework result: {result[:200] if result else 'None'}")
        if result:
            parsed = parse_json(result)
            print(f"rework parsed: {parsed}")
            if parsed:
                for k in ('title','description','suggestion'):
                    if parsed.get(k):
                        pending['analysis'][k] = parsed[k]
                save_json(PENDING_FILE, pending)
                _send_proposal(pending)
                return
        tg("⚠️ AI не ответил, показываю без изменений.")
        _send_proposal(pending)
        return
    analysis = pending.get('analysis', {})
    found_date = analysis.get('news_date', '') or (
        analysis.get('event_day','') + ' ' + analysis.get('event_month','') + ' ' + analysis.get('event_year','')
    ).strip()
    current_date = pending.get('date', '')

    # Если дата ещё не подтверждена пользователем
    if not pending.get('date_confirmed'):
        if found_date:
            date_btns = {'inline_keyboard': [[
                {'text': f'✅ {found_date}', 'callback_data': f'date_confirm_{found_date}'},
                {'text': '✏️ Ввести вручную', 'callback_data': 'date_manual'}
            ]]}
            tg(f"📅 <b>Дата материала:</b> <b>{found_date}</b>\n\nПодтверди или введи вручную:", reply_markup=date_btns)
        else:
            date_btns = {'inline_keyboard': [[
                {'text': f'📅 Сегодня ({current_date})', 'callback_data': f'date_confirm_{current_date}'},
                {'text': '✏️ Ввести вручную', 'callback_data': 'date_manual'}
            ]]}
            tg(f"📅 <b>Дата не найдена в статье.</b> Укажи дату публикации:", reply_markup=date_btns)
        pending['status'] = 'waiting_date'
        pending['user_comment'] = user_comment
        save_json(PENDING_FILE, pending)
        return

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
        page_anchors = {
            'news-consulting':   '#news-consulting',
            'news-drone':        '#news-drone',
            'events-consulting': '#events-consulting',
            'events-drone':      '#events-drone',
        }
        placements_for_link = pending['analysis'].get('placements', [])
        anchor = page_anchors.get(placements_for_link[0], '') if placements_for_link else ''
        site_link = SITE_URL + anchor

        tg(
            f"✅ <b>Готово! Сайт обновлён.</b>\n\n"
            f"🔗 {site_link}"
            + queue_note,
            reply_markup=rollback_btn
        )
        # После подтверждения — сначала проверяем приоритетную очередь
        manual_item = manual_queue_pop()
        if manual_item:
            propose(manual_item)
        elif analyzed:
            next_item = analyzed.pop(0)
            save_json(ANALYZED_FILE, analyzed)
            propose(next_item)
    else:
        tg("❌ Ошибка при записи в GitHub.")



# ── Диалоговая история ───────────────────────────────────────

DIALOG_FILE = 'data/dialog.json'
DIALOG_MAX  = 6


def dialog_add(role, text):
    history = load_json(DIALOG_FILE, [])
    history.append({'role': role, 'text': text[:500], 'ts': datetime.now().isoformat()})
    if len(history) > 20:
        history = history[-20:]
    save_json(DIALOG_FILE, history)

def dialog_clear():
    save_json(DIALOG_FILE, [])

def dialog_recent():
    history = load_json(DIALOG_FILE, [])
    recent  = history[-DIALOG_MAX:]
    lines   = []
    for m in recent:
        prefix = 'Пользователь' if m['role'] == 'user' else 'Бот'
        lines.append(f"{prefix}: {m['text']}")
    return '\n'.join(lines)


# ── Контекст сайта для Gemini ────────────────────────────────

def build_site_context():
    """Читает news.json и events.json, возвращает компактный текст с ID."""
    news_raw,   _ = gh_read(NEWS_FILE)
    events_raw, _ = gh_read(EVENTS_FILE)
    lines = ["=== ТЕКУЩИЙ КОНТЕНТ САЙТА ==="]

    if news_raw:
        try:
            news = json.loads(news_raw)
            for section, items in news.items():
                label = 'Консалтинг' if section == 'consulting' else 'Дроны'
                lines.append(f"\n[Новости / {label}]")
                for item in items[:15]:
                    lines.append(
                        f"  id={item.get('id')} | {item.get('date','')} | "
                        f"{item.get('title','')} | {item.get('text','')[:80]}"
                    )
        except: pass

    if events_raw:
        try:
            events = json.loads(events_raw)
            for section, types in events.items():
                label = 'Консалтинг' if section == 'consulting' else 'Дроны'
                for etype, items in types.items():
                    etype_label = 'предстоящие' if etype == 'upcoming' else 'прошедшие'
                    lines.append(f"\n[Мероприятия / {label} / {etype_label}]")
                    for item in items[:15]:
                        lines.append(
                            f"  id={item.get('id')} | "
                            f"{item.get('day','')} {item.get('month','')} {item.get('year','')} | "
                            f"{item.get('title','')} | {item.get('text','')[:80]}"
                        )
        except: pass

    return '\n'.join(lines)


# ── Gemini dialog ────────────────────────────────────────────

GEMINI_SYSTEM = """Ты — умный редактор сайта Александра Пронина (a-pronin.ru).
Сайт состоит из четырёх разделов:
  news-consulting    — Новости / Консалтинг
  news-drone         — Новости / Дроны
  events-consulting  — Мероприятия / Консалтинг (поля: day, month, year, type=upcoming|past)
  events-drone       — Мероприятия / Дроны (поля: day, month, year, type=upcoming|past)

Ты получаешь:
1. Текущий контент сайта (с ID каждой записи)
2. Историю диалога
3. Сообщение пользователя (текст, расшифровка голоса, или пересланный материал)
4. Содержимое ссылки (если пользователь прислал URL)

Твоя задача — понять намерение и вернуть JSON.

ПРАВИЛА:
- ДОБАВИТЬ материал → сформируй карточку из содержимого ссылки/текста, сам предложи раздел и дату
- ИЗМЕНИТЬ запись → найди по id, покажи что изменится
- УДАЛИТЬ → найди по id, покажи что удалишь
- ПЕРЕНЕСТИ мероприятие → найди по id, покажи перенос
- ВОПРОС → ответь текстом, action=null
- НЕ ПОНЯЛ → уточни, action=null
- Профиль не трогай — только news и events
- Пиши reply по-русски, живо и кратко — как будто объясняешь коллеге

Формат reply когда предлагаешь карточку — СТРОГО такой:
"Предлагаю добавить в [Новости / Консалтинг | Новости / Дроны | Мероприятия / Консалтинг | Мероприятия / Дроны]:

📌 [Заголовок]
[Описание 1-2 предложения]
📅 [дата в формате ДД месяца ГГГГ, например 4 июня 2026]
🔗 [ссылка]"

Никаких других форматов. Всегда включай все четыре поля.

Отвечай СТРОГО в JSON без markdown:
{
  "reply": "текст ответа",
  "show_confirm": true или false,
  "action": null | {
    "type": "add_news" | "add_event" | "edit" | "delete" | "move_event",
    "section": "consulting" | "drone",
    "id": null | <число>,
    "data": { ...поля... }
  }
}

Для add_news data: title, text, date, link, source
Для add_event data: title, text, day, month, year, type, link, link_text
Для edit data: id + только изменяемые поля
Для delete data: id
Для move_event data: id, new_type ("upcoming" или "past")

ФОРМАТ ДАТ — строго родительный падеж: "31 мая 2026", "2 июня 2026", "14 июля 2025".
НИКОГДА не пиши: "май", "июн", "июл", "янв" и т.д. — только полное слово в родительном падеже.

show_confirm=true когда есть action (нужно подтверждение).
show_confirm=false когда просто отвечаем на вопрос."""


def gemini_dialog(user_text, url_content=''):
    """Основной диалоговый вызов Gemini. Возвращает dict или None."""
    site_context = build_site_context()
    dialog_hist  = dialog_recent()

    url_section = f"\n\nСОДЕРЖИМОЕ ССЫЛКИ:\n{url_content[:2000]}" if url_content else ''

    prompt = f"""{GEMINI_SYSTEM}

{site_context}

ИСТОРИЯ ДИАЛОГА:
{dialog_hist if dialog_hist else '(нет)'}

СООБЩЕНИЕ ПОЛЬЗОВАТЕЛЯ:
{user_text}{url_section}"""

    resp = claude(prompt, max_tokens=800)
    if not resp:
        return None
    return parse_json(resp)


# ── Единая карточка предложения ──────────────────────────────

def show_proposal(reply, action, pending=None):
    """Показывает карточку с кнопками ДА/НЕТ. Фиксированный формат."""
    buttons = {'inline_keyboard': [[
        {'text': '✅ ДА', 'callback_data': 'confirm_yes'},
        {'text': '❌ НЕТ', 'callback_data': 'confirm_no'},
    ]]}

    card_text = None
    if action and action.get('data'):
        data    = action['data']
        atype   = action.get('type', '')
        section = action.get('section', '')

        section_label = PLACEMENT_LABELS.get(
            f"{'news' if 'news' in atype else 'events'}-{section}", ''
        )

        title  = data.get('title', '')
        desc   = data.get('text', '') or data.get('description', '')
        link   = data.get('link', '')
        source = ''
        if pending:
            source = pending.get('source', '')
        if not source:
            source = data.get('source', '')

        if atype == 'add_news':
            date = data.get('date', '')
        elif atype == 'add_event':
            day   = data.get('day', '')
            month = data.get('month', '')
            year  = data.get('year', '')
            date  = f"{day} {month} {year}".strip()
        else:
            date = ''

        lines = ['🤖 <b>Клод предлагает</b>\n']
        if source: lines.append(f'📌 Источник: {source}')
        if link:   lines.append(f'🔗 Ссылка: {link}')
        if date:   lines.append(f'📅 Дата: {date}')
        if section_label:
            lines.append(f'\n💡 Предложение: разместить в <b>{section_label}</b>\n')
        lines.append('📋 <b>Как будет выглядеть:</b>')
        if title: lines.append(f'<b>{title}</b>')
        if desc:  lines.append(f'<i>{desc}</i>')

        card_text = '\n'.join(lines)

    text_to_send = card_text if card_text else reply

    # Добавляем заметку об очереди если есть
    if card_text and reply:
        for line in reply.split('\n'):
            if '📋' in line and 'очереди' in line:
                text_to_send += f'\n\n{line}'
                break

    tg(text_to_send, reply_markup=buttons)


def show_done(site_link, queue_note=''):
    """Показывает сообщение об успехе с кнопками отката и подтверждения."""
    buttons = {'inline_keyboard': [[
        {'text': '↩️ Откатить', 'callback_data': 'confirm_rollback'},
        {'text': '👍 Принято',  'callback_data': 'confirm_done'},
    ]]}
    tg(
        f"✅ <b>Готово! Сайт обновлён.</b>\n\n🔗 {site_link}{queue_note}",
        reply_markup=buttons
    )


# ── Снапшот для отката ───────────────────────────────────────

SNAPSHOT_FILE = 'data/snapshot.json'

def snapshot_save():
    """Сохраняет текущее состояние news.json и events.json."""
    try:
        headers = {'Authorization': f'token {GITHUB_TOKEN}',
                   'Accept': 'application/vnd.github.v3+json'}
        snapshot = {}
        for filepath in [NEWS_FILE, EVENTS_FILE]:
            r = requests.get(
                f'https://api.github.com/repos/{GITHUB_REPO}/contents/{filepath}',
                headers=headers, timeout=15)
            if r.status_code == 200:
                snapshot[filepath] = {
                    'content': r.json()['content'],
                    'sha':     r.json()['sha']
                }
        save_json(SNAPSHOT_FILE, snapshot)
        print(f"snapshot_save: OK")
    except Exception as e:
        print(f"snapshot_save ошибка: {e}")

def snapshot_restore():
    """Восстанавливает news.json и events.json из снапшота."""
    snapshot = load_json(SNAPSHOT_FILE, {})
    if not snapshot:
        return False
    headers = {'Authorization': f'token {GITHUB_TOKEN}',
               'Accept': 'application/vnd.github.v3+json'}
    success = True
    for filepath, data in snapshot.items():
        r = requests.get(
            f'https://api.github.com/repos/{GITHUB_REPO}/contents/{filepath}',
            headers=headers, timeout=15)
        if r.status_code != 200:
            success = False; continue
        current_sha = r.json().get('sha')
        r2 = requests.put(
            f'https://api.github.com/repos/{GITHUB_REPO}/contents/{filepath}',
            headers=headers,
            json={
                'message': f'Rollback: восстановление {filepath}',
                'content': data['content'],
                'sha': current_sha
            }, timeout=30)
        if r2.status_code not in (200, 201):
            print(f"snapshot_restore ошибка {filepath}: {r2.text[:200]}")
            success = False
    return success


# ── Выполнение действия ──────────────────────────────────────

def execute_action(action):
    """Выполняет действие из ответа Gemini. Возвращает (ok, site_link)."""
    atype   = action.get('type')
    section = action.get('section', '')
    data    = action.get('data', {})

    # Сохраняем снапшот перед любым изменением
    snapshot_save()

    headers = {'Authorization': f'token {GITHUB_TOKEN}',
               'Accept': 'application/vnd.github.v3+json'}

    def read_json_file(filepath):
        r = requests.get(
            f'https://api.github.com/repos/{GITHUB_REPO}/contents/{filepath}',
            headers=headers, timeout=15)
        if r.status_code != 200:
            return None, None
        sha = r.json().get('sha')
        obj = json.loads(base64.b64decode(
            r.json()['content'].replace('\n','').replace(' ','')).decode('utf-8'))
        return obj, sha

    def write_json_file(filepath, obj, msg, sha):
        return gh_write(filepath, json.dumps(obj, ensure_ascii=False, indent=2), msg, sha=sha)

    ANCHORS = {
        'news-consulting': '#news-consulting', 'news-drone': '#news-drone',
        'events-consulting': '#events-consulting', 'events-drone': '#events-drone',
    }

    if atype == 'add_news':
        news, sha = read_json_file(NEWS_FILE)
        if news is None: return False, ''
        if section not in news: news[section] = []
        link = data.get('link', '')
        if link and any(e.get('link') == link for e in news[section]):
            return True, SITE_URL + ANCHORS.get(f'news-{section}', '')
        news[section].insert(0, {
            'id':      int(datetime.now().timestamp()),
            'section': section,
            'source':  data.get('source', ''),
            'date':    data.get('date', datetime.now().strftime('%d %b %Y')),
            'title':   data.get('title', ''),
            'text':    data.get('text', ''),
            'link':    link
        })
        ok = write_json_file(NEWS_FILE, news, f"Новость: {data.get('title','')[:50]}", sha)
        return ok, SITE_URL + ANCHORS.get(f'news-{section}', '')

    elif atype == 'add_event':
        events, sha = read_json_file(EVENTS_FILE)
        if events is None: return False, ''
        if section not in events: events[section] = {'upcoming': [], 'past': []}
        etype = data.get('type', 'upcoming')
        link  = data.get('link', '')
        events[section][etype].insert(0, {
            'id':        int(datetime.now().timestamp()),
            'section':   section,
            'type':      etype,
            'day':       data.get('day', ''),
            'month':     data.get('month', ''),
            'year':      data.get('year', str(datetime.now().year)),
            'title':     data.get('title', ''),
            'text':      data.get('text', ''),
            'link':      link,
            'link_text': data.get('link_text', 'Подробнее')
        })
        ok = write_json_file(EVENTS_FILE, events, f"Мероприятие: {data.get('title','')[:50]}", sha)
        return ok, SITE_URL + ANCHORS.get(f'events-{section}', '')

    elif atype == 'edit':
        # Gemini может вернуть data как dict (одна запись) или list (несколько записей)
        edits = data if isinstance(data, list) else [data]

        for filepath, anchor_pfx in [(NEWS_FILE, 'news'), (EVENTS_FILE, 'events')]:
            obj, sha = read_json_file(filepath)
            if obj is None: continue
            found_sec = ''
            changed   = False

            for edit_data in edits:
                edit_id = str(action.get('id') or edit_data.get('id', ''))
                if not edit_id:
                    continue
                # Ищем запись во всех разделах и типах
                for sec, val in obj.items():
                    if isinstance(val, list):
                        items_list = val
                    elif isinstance(val, dict):
                        items_list = [i for sub in val.values() for i in (sub if isinstance(sub, list) else [])]
                    else:
                        continue
                    for item in items_list:
                        if str(item.get('id')) == edit_id:
                            for k, v in edit_data.items():
                                if k != 'id': item[k] = v
                            found_sec = sec
                            changed   = True
                            break
                # Не прерываем внешний цикл — обрабатываем все edits

            if changed:
                ok = write_json_file(filepath, obj, f"Правка (batch {len(edits)})", sha)
                return ok, SITE_URL + ANCHORS.get(f'{anchor_pfx}-{found_sec}', '')
        return False, ''

    elif atype == 'delete':
        item_id = str(action.get('id') or data.get('id', ''))
        for filepath in [NEWS_FILE, EVENTS_FILE]:
            obj, sha = read_json_file(filepath)
            if obj is None: continue
            found = False
            found_sec = ''
            anchor_pfx = 'news' if filepath == NEWS_FILE else 'events'
            for sec, val in obj.items():
                if isinstance(val, list):
                    before = len(val); obj[sec] = [x for x in val if str(x.get('id')) != item_id]
                    if len(obj[sec]) < before: found = True; found_sec = sec; break
                elif isinstance(val, dict):
                    for etype, items in val.items():
                        before = len(items); val[etype] = [x for x in items if str(x.get('id')) != item_id]
                        if len(val[etype]) < before: found = True; found_sec = sec; break
                if found: break
            if found:
                ok = write_json_file(filepath, obj, f"Удаление id={item_id}", sha)
                return ok, SITE_URL + ANCHORS.get(f'{anchor_pfx}-{found_sec}', '')

    elif atype == 'move_event':
        item_id  = str(action.get('id') or data.get('id', ''))
        new_type = data.get('new_type', 'past')
        events, sha = read_json_file(EVENTS_FILE)
        if events is None: return False, ''
        found_sec = ''
        for sec, types in events.items():
            for etype, items in types.items():
                for item in items:
                    if str(item.get('id')) == item_id:
                        types[etype] = [x for x in items if str(x.get('id')) != item_id]
                        item['type'] = new_type
                        types.setdefault(new_type, []).insert(0, item)
                        found_sec = sec; break
                if found_sec: break
            if found_sec: break
        if found_sec:
            ok = write_json_file(EVENTS_FILE, events, f"Перенос id={item_id} → {new_type}", sha)
            return ok, SITE_URL + ANCHORS.get(f'events-{found_sec}', '')

    return False, ''


# ── do_rollback (универсальный) ──────────────────────────────

def do_rollback(pending):
    """Откат через снапшот — работает для любого действия."""
    tg("🔄 <b>Откатываю...</b>")

    # Новый флоу — восстанавливаем из снапшота
    if pending.get('action'):
        ok = snapshot_restore()
        if ok:
            pending_clear()
            action  = pending.get('action', {})
            section = action.get('section', '')
            atype   = action.get('type', '')
            ANCHORS = {
                'consulting': {'news': '#news-consulting', 'events': '#events-consulting'},
                'drone':      {'news': '#news-drone',      'events': '#events-drone'},
            }
            if 'news' in atype:
                anchor = ANCHORS.get(section, {}).get('news', '')
            elif 'event' in atype:
                anchor = ANCHORS.get(section, {}).get('events', '')
            else:
                anchor = ANCHORS.get(section, {}).get('news', '') or ANCHORS.get(section, {}).get('events', '')
            tg(f"✅ <b>Откат выполнен!</b>\n🔗 {SITE_URL}{anchor}")
        else:
            tg("❌ Не удалось откатить — снапшот недоступен.")
        return

    # Старый флоу — placements (обратная совместимость)
    success = True
    placements = pending.get('analysis', {}).get('placements', [])
    for placement in placements:
        if placement.startswith('news-'):
            section  = placement.replace('news-', '')
            raw, sha = gh_read(NEWS_FILE)
            if raw:
                news = json.loads(raw)
                if news.get(section):
                    news[section].pop(0)
                    ok = gh_write(NEWS_FILE, json.dumps(news, ensure_ascii=False, indent=2),
                                 'Rollback новости', sha=sha)
                    if not ok: success = False
        elif placement.startswith('events-'):
            section  = placement.replace('events-', '')
            raw, sha = gh_read(EVENTS_FILE)
            if raw:
                events = json.loads(raw)
                if events.get(section, {}).get('upcoming'):
                    events[section]['upcoming'].pop(0)
                    ok = gh_write(EVENTS_FILE, json.dumps(events, ensure_ascii=False, indent=2),
                                 'Rollback мероприятия', sha=sha)
                    if not ok: success = False

    if success:
        pending_clear()
        anchor = ''
        if placements:
            p = placements[0]
            if p.startswith('news-'):
                anchor = f'#news-{p.replace("news-", "")}'
            elif p.startswith('events-'):
                anchor = f'#events-{p.replace("events-", "")}'
        tg(f"✅ <b>Откат выполнен!</b>\n🔗 {SITE_URL}{anchor}")
    else:
        tg("❌ Ошибка отката.")


# ── propose — теперь через gemini_dialog ────────────────────

def propose(item):
    """Единая точка входа для показа предложения.
    Мониторинг и ручной ввод используют один и тот же путь."""
    text   = item.get('text', '')
    link   = item.get('link', '')
    source = item.get('source', '')

    tg("🔍 <b>Анализирую...</b>")

    # Читаем ссылку — точно так же как при ручном вводе
    url_content = ''
    if link:
        url_content = fetch_url_content(link) or ''

    # Формируем сообщение точно как при ручном вводе ссылки
    if link:
        user_msg = link
    else:
        user_msg = text[:500]

    # Вызываем gemini_dialog — та же функция что при ручном вводе
    result = gemini_dialog(user_msg, url_content=url_content)

    if not result:
        tg("❌ Ошибка анализа. Пропускаю.")
        analyzed = load_json(ANALYZED_FILE, [])
        if analyzed:
            next_item = analyzed.pop(0)
            save_json(ANALYZED_FILE, analyzed)
            propose(next_item)
        return

    reply  = result.get('reply', '')
    action = result.get('action')

    if not action:
        tg(f"ℹ️ {reply}")
        analyzed = load_json(ANALYZED_FILE, [])
        if analyzed:
            next_item = analyzed.pop(0)
            save_json(ANALYZED_FILE, analyzed)
            propose(next_item)
        return

    # Сохраняем pending и показываем карточку — та же логика что в process_commands
    save_json(PENDING_FILE, {
        'status':    'dialog_confirm',
        'reply':     reply,
        'action':    action,
        'source':    source,
        'user_text': user_msg,
        'created':   datetime.now().isoformat()
    })
    dialog_add('bot', reply)

    analyzed = load_json(ANALYZED_FILE, [])
    q_len    = queue_len() + len(analyzed)
    queue_note = f"\n\n📋 <i>Ещё в очереди: {q_len}</i>" if q_len > 0 else ""

    show_proposal(reply + queue_note, action, pending=load_json(PENDING_FILE, None))


# ── Автопринятие ─────────────────────────────────────────────

AUTO_ACCEPT_MINUTES = 210  # 3.5 часа

def auto_accept_if_expired():
    """Если pending в статусе done больше 210 минут — принимаем автоматически."""
    pending = pending_load()
    if not pending or pending.get('status') != 'done':
        return
    created = pending.get('created', '')
    if not created:
        return
    try:
        created_dt = datetime.fromisoformat(created)
        elapsed = (datetime.now() - created_dt).total_seconds() / 60
        if elapsed >= AUTO_ACCEPT_MINUTES:
            print(f"auto_accept: прошло {elapsed:.0f} мин — принимаю автоматически")
            pending_clear()
            tg(f"👍 Принято автоматически (прошло {elapsed:.0f} мин)")
            analyzed = load_json(ANALYZED_FILE, [])
            if analyzed:
                next_item = analyzed.pop(0)
                save_json(ANALYZED_FILE, analyzed)
                propose(next_item)
            else:
                manual_item = manual_queue_pop()
                if manual_item:
                    propose(manual_item)
    except Exception as e:
        print(f"auto_accept ошибка: {e}")


# ── Новый process_commands ───────────────────────────────────

def process_commands():
    auto_accept_if_expired()
    last_id = get_last_uid()
    updates = get_updates(offset=(last_id + 1) if last_id else None)

    for upd in updates:
        uid = upd['update_id']
        save_last_uid(uid)
        pending = pending_load()

        # ── Inline-кнопки ──
        cb = upd.get('callback_query')
        if cb:
            cb_cid = str(cb.get('from', {}).get('id', ''))
            if cb_cid == str(CHAT_ID):
                tg_answer_callback(cb['id'])
                cb_data = cb.get('data', '')
                pending = pending_load()

                if cb_data == 'confirm_yes' and pending and pending.get('status') == 'dialog_confirm':
                    action = pending.get('action')
                    if action:
                        tg("⚙️ <b>Вношу изменения...</b>")
                        ok, site_link = execute_action(action)
                        if ok:
                            pending['status'] = 'done'
                            save_json(PENDING_FILE, pending)
                            analyzed = load_json(ANALYZED_FILE, [])
                            q_len    = queue_len() + len(analyzed)
                            queue_note = f"\n\n📋 <i>Ещё в очереди: {q_len}</i>" if q_len > 0 else ""
                            show_done(site_link, queue_note)
                            dialog_add('bot', f"Выполнено: {action.get('type')} → {site_link}")
                        else:
                            tg("❌ Ошибка при записи в GitHub.")
                    else:
                        pending_clear()

                elif cb_data == 'confirm_no' and pending and pending.get('status') == 'dialog_confirm':
                    pending_clear()
                    tg("⏭ Отменено. Напиши что хочешь изменить.")
                    dialog_add('bot', 'Действие отменено.')

                elif cb_data == 'confirm_rollback' and pending and pending.get('status') == 'done':
                    do_rollback(pending)
                    dialog_add('bot', 'Выполнен откат.')

                elif cb_data == 'confirm_yes' and pending and pending.get('status') == 'waiting_confirm':
                    tg("✅ ДА"); do_confirm(pending)
                elif cb_data == 'confirm_no' and pending and pending.get('status') == 'waiting_confirm':
                    tg("❌ НЕТ"); pending_clear(); tg("⏭ Пропущено.")
                    manual_item = manual_queue_pop()
                    if manual_item: propose(manual_item)
                    else:
                        analyzed = load_json(ANALYZED_FILE, [])
                        if analyzed:
                            first = analyzed.pop(0); save_json(ANALYZED_FILE, analyzed); propose(first)
                elif cb_data.startswith('date_confirm_') and pending and pending.get('status') == 'waiting_date':
                    confirmed_date = cb_data.replace('date_confirm_', '')
                    pending['date'] = confirmed_date; pending['date_confirmed'] = True
                    pending['status'] = 'waiting_confirm'; save_json(PENDING_FILE, pending)
                    do_confirm(pending)
                elif cb_data == 'date_manual' and pending and pending.get('status') == 'waiting_date':
                    tg("✏️ Введи дату вручную: <b>31 мая 2026</b>")
                elif cb_data == 'confirm_done' and pending and pending.get('status') == 'done':
                    pending_clear()
                    tg("👍 Принято.")
                    # Показываем следующий материал из очереди если есть
                    analyzed = load_json(ANALYZED_FILE, [])
                    if analyzed:
                        next_item = analyzed.pop(0)
                        save_json(ANALYZED_FILE, analyzed)
                        propose(next_item)
                    else:
                        manual_item = manual_queue_pop()
                        if manual_item:
                            propose(manual_item)
            continue

        msg = upd.get('message', {})
        cid = str(msg.get('chat', {}).get('id', ''))
        if cid != str(CHAT_ID):
            continue

        # ── Определяем текст ──
        text    = msg.get('text', '').strip()
        voice   = msg.get('voice')
        forward = msg.get('forward_origin') or msg.get('forward_from') or msg.get('forward_from_chat')

        if voice and not text:
            tg("🎤 <b>Распознаю...</b>")
            text = transcribe_voice(voice['file_id'])
            if not text:
                tg("❌ Не удалось распознать. Напиши текстом.")
                continue
            tg(f"📝 <i>{text}</i>")

        if forward and not text:
            text = (msg.get('text', '') or msg.get('caption', '')).strip()

        if not text:
            continue

        print(f"← {text[:100]}")
        tl      = text.lower().strip()
        pending = pending_load()

        # ── Служебные команды ──
        if tl in ('/help', '/start'):
            tg(
                '🤖 <b>Редактор сайта a-pronin.ru</b>\n\n'
                'Пиши или надиктовывай что угодно:\n'
                '• Пришли ссылку — предложу карточку\n'
                '• "Перенеси Елагин в прошедшие"\n'
                '• "Измени описание новости про BITOBE"\n'
                '• "Что сейчас в новостях дронов?"\n'
                '• "Удали последнее мероприятие консалтинга"\n\n'
                '/status — текущее состояние\n'
                '/clear — сбросить историю диалога'
            )
            continue

        if tl == '/status':
            p = pending_load()
            lines = [f"⏳ Статус: {p.get('status')}" if p else "✅ Нет ожидающих действий"]
            if p and p.get('action'):
                lines.append(f"Действие: {p['action'].get('type')}")
            q_len = queue_len()
            if q_len: lines.append(f"📋 В очереди: {q_len}")
            tg('\n'.join(lines))
            continue

        if tl == '/clear':
            dialog_clear()
            tg("🗑 История диалога сброшена.")
            continue

        # ── Ввод даты (обратная совместимость) ──
        if pending and pending.get('status') == 'waiting_date':
            norm_prompt = f'Преобразуй дату "{text}" в формат "ДД месяц ГГГГ" на русском, например "31 мая 2026". Ответь только датой.'
            normalized = claude(norm_prompt, max_tokens=30)
            confirmed_date = normalized.strip() if normalized else text
            pending['date'] = confirmed_date; pending['date_confirmed'] = True
            pending['status'] = 'waiting_confirm'; save_json(PENDING_FILE, pending)
            do_confirm(pending, user_comment=pending.get('user_comment', ''))
            continue

        # ── Всё остальное — в Gemini ──
        dialog_add('user', text)

        # Если pending в статусе done и пришёл новый материал — считаем принятым
        if pending and pending.get('status') == 'done':
            pending_clear()
            tg("👍 Предыдущее принято.")

        pending = pending_load()

        # Текстовое да/нет при ожидании подтверждения
        if pending and pending.get('status') == 'dialog_confirm':
            if tl in ('да', 'yes', 'ок', 'ok', '+', 'давай', 'вноси', '✅'):
                action = pending.get('action')
                if action:
                    tg("⚙️ <b>Вношу изменения...</b>")
                    ok, site_link = execute_action(action)
                    if ok:
                        pending['status'] = 'done'
                        save_json(PENDING_FILE, pending)
                        analyzed = load_json(ANALYZED_FILE, [])
                        q_len    = queue_len() + len(analyzed)
                        queue_note = f"\n\n📋 <i>Ещё в очереди: {q_len}</i>" if q_len > 0 else ""
                        show_done(site_link, queue_note)
                        dialog_add('bot', f"Выполнено: {action.get('type')} → {site_link}")
                    else:
                        tg("❌ Ошибка при записи в GitHub.")
                continue
            elif tl in ('нет', 'no', '-', 'отмена', 'пропустить', 'skip', '❌'):
                pending_clear()
                tg("⏭ Отменено.")
                dialog_add('bot', 'Действие отменено.')
                continue
            # Иначе — уточнение к карточке
            # Если карточка — add_news или add_event, применяем правку к самой карточке
            action = pending.get('action', {})
            if action.get('type') in ('add_news', 'add_event'):
                tg("🤔 <b>Переделываю карточку...</b>")
                card_prompt = f"""Есть предложение добавить запись:
{json.dumps(action, ensure_ascii=False, indent=2)}

Пользователь хочет внести правку: "{text}"

Верни обновлённый action с теми же полями но с исправлениями.
Отвечай СТРОГО в JSON без markdown:
{{"reply": "текст подтверждения правки", "show_confirm": true, "action": {{...обновлённый action...}}}}"""
                result = parse_json(claude(card_prompt, max_tokens=600))
                if result and result.get('action'):
                    reply = result.get('reply', '')
                    dialog_add('bot', reply)
                    save_json(PENDING_FILE, {
                        'status':    'dialog_confirm',
                        'reply':     reply,
                        'action':    result['action'],
                        'user_text': text,
                        'created':   datetime.now().isoformat()
                    })
                    show_proposal(reply, result['action'])
                else:
                    tg("❌ Не смог применить правку. Попробуй ещё раз.")
                continue

        # Читаем ссылку если есть
        urls        = re.findall(r'https?://\S+', text)
        url_content = ''
        if urls:
            tg("🔍 <b>Читаю ссылку...</b>")
            url_content = fetch_url_content(urls[0]) or ''

        tg("🤔 <b>Думаю...</b>")
        result = gemini_dialog(text, url_content=url_content)

        if not result:
            tg("❌ Gemini не ответил. Попробуй ещё раз.")
            continue

        reply        = result.get('reply', '')
        action       = result.get('action')
        show_confirm = result.get('show_confirm', False)

        dialog_add('bot', reply)

        if action and show_confirm:
            save_json(PENDING_FILE, {
                'status':    'dialog_confirm',
                'reply':     reply,
                'action':    action,
                'source':    'Вручную от пользователя',
                'user_text': text,
                'created':   datetime.now().isoformat()
            })
            show_proposal(reply, action, pending=load_json(PENDING_FILE, None))
        else:
            pending_clear()
            tg(reply)

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
        propose(manual_item)
        return

    # Проверяем — есть ли уже проанализированные материалы
    analyzed = load_json(ANALYZED_FILE, [])
    if analyzed:
        # Уже есть готовые — показываем первый
        print(f"process_queue_batch: есть {len(analyzed)} проанализированных, показываю первый")
        item = analyzed.pop(0)
        save_json(ANALYZED_FILE, analyzed)
        if item.get('skip'):
            # Уже на сайте — уведомляем и берём следующий
            reason = item.get('reason', '')
            title  = item.get('title', item.get('text', '')[:60])
            tg(f"ℹ️ <b>Пропущено</b> — уже на сайте:\n<i>{title}</i>"
               + (f"\n{reason}" if reason else ""))
            process_queue_batch()
            return
        propose(item)
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
        propose(first)


# ── Главная ─────────────────────────────────────────────────

def run():
    now = datetime.now().strftime('%d.%m.%Y %H:%M')
    print(f"=== Запуск {now} ===")

    # 1. Обрабатываем сообщения пользователя
    process_commands()

    # 2. Мониторинг — добавляет новые материалы в очередь
    pending = pending_load()
    if pending and pending.get('status') in ('waiting_confirm', 'done', 'dialog_confirm', 'waiting_date'):
        print("Есть pending — мониторинг пропускаю")
    else:
        tg(f"🔍 <b>Запускаю мониторинг</b> — {now}\nПроверяю сайты и ВКонтакте...")

        state      = load_json(STATE_FILE, {})
        new_found  = 0

        for page in WEB_PAGES:
            print(f"Веб: {page['url']}")
            before = queue_len()
            process_web(page, state)
            new_found += queue_len() - before

        if VK_TOKEN:
            for page in VK_PAGES:
                print(f"ВК: vk.com/{page['screen_name']}")
                before = queue_len()
                process_vk(page, state)
                new_found += queue_len() - before
        else:
            print("VK_TOKEN не задан")

        save_json(STATE_FILE, state)

        if new_found > 0:
            tg(f"📥 Найдено новых упоминаний: <b>{new_found}</b>. Анализирую...")
        else:
            tg(f"✅ <b>Мониторинг завершён</b> — новых упоминаний нет.")

        # 3. Пакетный анализ очереди и показ первого результата
        if not pending_load():
            process_queue_batch()

    print("=== Готово ===")


if __name__ == '__main__':
    import sys
    if '--commands-only' in sys.argv:
        print(f"=== Команды {__import__('datetime').datetime.now().strftime('%d.%m.%Y %H:%M')} ===")
        process_commands()
        # Если есть материалы в очереди и нет активного pending — обрабатываем
        pending = pending_load()
        if queue_len() > 0 or len(load_json(ANALYZED_FILE, [])) > 0:
            if not pending or pending.get('status') not in ('waiting_confirm', 'done', 'dialog_confirm', 'waiting_date'):
                print("Очередь не пуста — обрабатываю...")
                process_queue_batch()
        print("=== Готово ===")
    else:
        run()
