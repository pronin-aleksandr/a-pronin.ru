import requests
from bs4 import BeautifulSoup
import json
import os
import hashlib
from datetime import datetime
import base64

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')
VK_TOKEN = os.environ.get('VK_TOKEN')
GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN')
GITHUB_REPO = 'pronin-aleksandr/a-pronin.ru'

WEB_PAGES = [
    {'url': 'https://bitobe.ru/team/4573/', 'section': 'Консалтинг', 'keywords': ['Пронин']},
    {'url': 'https://fgd.spb.ru/', 'section': 'Дроны', 'keywords': ['Пронин']},
]

VK_PAGES = [
    {'screen_name': 'sansei4', 'section': 'Консалтинг/Дроны', 'keywords': ['Пронин', 'BITOBE', 'дрон', 'FPV', 'БПЛА']},
    {'screen_name': 'bitobe', 'section': 'Консалтинг', 'keywords': ['Пронин']},
    {'screen_name': 'fgdrus', 'section': 'Дроны', 'keywords': ['Пронин']},
    {'screen_name': 'fgdpskov', 'section': 'Дроны', 'keywords': ['Пронин']},
    {'screen_name': 'fgdspb', 'section': 'Дроны', 'keywords': ['Пронин']},
]

STATE_FILE = 'data/state.json'
NEWS_FILE = 'data/news.json'
EVENTS_FILE = 'data/events.json'
UPDATES_FILE = 'data/last_update_id.txt'

# ─── Утилиты ───────────────────────────────────────────────

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
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        r = requests.get(url, headers=headers, timeout=15)
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
    end = min(len(text), idx + len(keyword) + context)
    snippet = ' '.join(text[start:end].split())
    if start > 0: snippet = '...' + snippet
    if end < len(text): snippet += '...'
    return snippet

# ─── Telegram ──────────────────────────────────────────────

def send_telegram(message):
    url = f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage'
    try:
        requests.post(url, data={
            'chat_id': CHAT_ID, 'text': message,
            'parse_mode': 'HTML', 'disable_web_page_preview': True
        })
    except Exception as e:
        print(f"Telegram ошибка: {e}")

def get_updates(offset=None):
    url = f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates'
    params = {'timeout': 0, 'allowed_updates': ['message']}
    if offset:
        params['offset'] = offset
    try:
        r = requests.get(url, params=params, timeout=10)
        return r.json().get('result', [])
    except:
        return []

def get_last_update_id():
    if os.path.exists(UPDATES_FILE):
        with open(UPDATES_FILE, 'r') as f:
            try: return int(f.read().strip())
            except: return None
    return None

def save_last_update_id(uid):
    os.makedirs('data', exist_ok=True)
    with open(UPDATES_FILE, 'w') as f:
        f.write(str(uid))

# ─── GitHub: обновление файлов на сайте ────────────────────

def github_update_file(filepath, content_str, commit_msg):
    headers = {
        'Authorization': f'token {GITHUB_TOKEN}',
        'Accept': 'application/vnd.github.v3+json'
    }
    api_url = f'https://api.github.com/repos/{GITHUB_REPO}/contents/{filepath}'

    # Получаем текущий SHA файла
    r = requests.get(api_url, headers=headers)
    sha = r.json().get('sha') if r.status_code == 200 else None

    content_b64 = base64.b64encode(content_str.encode('utf-8')).decode('utf-8')
    payload = {'message': commit_msg, 'content': content_b64}
    if sha:
        payload['sha'] = sha

    r2 = requests.put(api_url, headers=headers, json=payload)
    return r2.status_code in (200, 201)

# ─── Обработка команд от пользователя ─────────────────────

def process_commands():
    last_id = get_last_update_id()
    updates = get_updates(offset=(last_id + 1) if last_id else None)

    for update in updates:
        uid = update['update_id']
        save_last_update_id(uid)

        msg = update.get('message', {})
        text = msg.get('text', '').strip()
        chat_id = str(msg.get('chat', {}).get('id', ''))

        if chat_id != str(CHAT_ID):
            continue

        print(f"Команда: {text}")

        # Команды:
        # /news Заголовок | Текст [| Ссылка]
        # /event Заголовок | Дата | Текст [| Ссылка]
        # /help

        if text.startswith('/news '):
            parts = text[6:].split('|')
            if len(parts) >= 2:
                news = load_json(NEWS_FILE, [])
                item = {
                    'id': int(datetime.now().timestamp()),
                    'title': parts[0].strip(),
                    'text': parts[1].strip(),
                    'link': parts[2].strip() if len(parts) > 2 else '',
                    'date': datetime.now().strftime('%d.%m.%Y')
                }
                news.insert(0, item)
                news = news[:20]  # Храним последние 20
                save_json(NEWS_FILE, news)
                ok = github_update_file('data/news.json', json.dumps(news, ensure_ascii=False, indent=2), f'Новость: {item["title"]}')
                if ok:
                    send_telegram(f'✅ Новость добавлена на сайт:\n<b>{item["title"]}</b>')
                else:
                    send_telegram('❌ Ошибка при добавлении новости')
            else:
                send_telegram('⚠️ Формат: /news Заголовок | Текст | Ссылка (необязательно)')

        elif text.startswith('/event '):
            parts = text[7:].split('|')
            if len(parts) >= 3:
                events = load_json(EVENTS_FILE, [])
                item = {
                    'id': int(datetime.now().timestamp()),
                    'title': parts[0].strip(),
                    'date': parts[1].strip(),
                    'text': parts[2].strip(),
                    'link': parts[3].strip() if len(parts) > 3 else '',
                    'added': datetime.now().strftime('%d.%m.%Y')
                }
                events.insert(0, item)
                events = events[:20]
                save_json(EVENTS_FILE, events)
                ok = github_update_file('data/events.json', json.dumps(events, ensure_ascii=False, indent=2), f'Анонс: {item["title"]}')
                if ok:
                    send_telegram(f'✅ Анонс добавлен на сайт:\n<b>{item["title"]}</b> — {item["date"]}')
                else:
                    send_telegram('❌ Ошибка при добавлении анонса')
            else:
                send_telegram('⚠️ Формат: /event Название | Дата | Описание | Ссылка (необязательно)')

        elif text in ('/help', '/start', 'help'):
            send_telegram(
                '🤖 <b>Команды Pronin Monitor</b>\n\n'
                '📰 <b>Добавить новость:</b>\n'
                '/news Заголовок | Текст | Ссылка\n\n'
                '📅 <b>Добавить анонс:</b>\n'
                '/event Название | Дата | Описание | Ссылка\n\n'
                '<i>Ссылка — необязательна</i>\n\n'
                '🔍 Мониторинг запускается автоматически каждый час.'
            )

# ─── ВКонтакте ─────────────────────────────────────────────

def get_vk_data(screen_name):
    try:
        r = requests.get('https://api.vk.com/method/utils.resolveScreenName', params={
            'screen_name': screen_name, 'access_token': VK_TOKEN, 'v': '5.131'
        }, timeout=10)
        data = r.json()
        if 'error' in data or not data.get('response'):
            print(f"  ВК не найден: {screen_name}")
            return None
        obj = data['response']
        owner_id = obj['object_id']
        if obj['type'] in ('group', 'page'):
            owner_id = -owner_id

        r2 = requests.get('https://api.vk.com/method/wall.get', params={
            'owner_id': owner_id, 'count': 50, 'filter': 'all',
            'access_token': VK_TOKEN, 'v': '5.131'
        }, timeout=10)
        wall = r2.json()
        if 'error' in wall:
            print(f"  ВК wall.get ошибка: {wall['error']}")
            return None
        posts = wall.get('response', {}).get('items', [])
        result = [{'text': p['text'], 'link': f"https://vk.com/wall{owner_id}_{p['id']}"}
                  for p in posts if p.get('text', '').strip()]
        print(f"  ВК {screen_name}: {len(result)} постов")
        return result, owner_id
    except Exception as e:
        print(f"  ВК ошибка {screen_name}: {e}")
        return None

# ─── Мониторинг ────────────────────────────────────────────

def process_web(page, state):
    url = page['url']
    html = fetch_page(url)
    if not html:
        return
    text = extract_text(html)
    current_hash = get_hash(text)
    found = [(kw, get_snippet(text, kw), url) for kw in page['keywords'] if get_snippet(text, kw)]

    if url not in state:
        state[url] = {'hash': current_hash, 'checked': datetime.now().isoformat()}
        msg = f"🚀 <b>Первичное сканирование</b>\n📌 {page['section']}\n\n"
        if found:
            for kw, snippet, link in found[:3]:
                msg += f"🔑 <b>{kw}</b>\n<i>{snippet}</i>\n🔗 {link}\n\n"
            msg += f"💡 Возможно стоит обновить раздел <b>{page['section']}</b> на сайте."
        else:
            msg += f"🔗 {url}\nℹ️ Упоминаний не найдено."
        send_telegram(msg)
    elif current_hash != state[url].get('hash'):
        state[url] = {'hash': current_hash, 'checked': datetime.now().isoformat()}
        msg = f"🔔 <b>Изменение!</b>\n📌 {page['section']}\n\n"
        if found:
            for kw, snippet, link in found[:3]:
                msg += f"🔑 <b>{kw}</b>\n<i>{snippet}</i>\n🔗 {link}\n\n"
            msg += f"💡 Обнови раздел <b>{page['section']}</b> на a-pronin.ru"
        else:
            msg += f"🔗 {url}\nℹ️ Страница изменилась, упоминаний не найдено."
        send_telegram(msg)
    else:
        state[url]['checked'] = datetime.now().isoformat()
        print(f"  Без изменений: {url}")

def process_vk(page, state):
    name = page['screen_name']
    key = f"vk_{name}"
    result = get_vk_data(name)
    if not result:
        return
    posts, owner_id = result
    if not posts:
        return

    all_text = '\n'.join(p['text'] for p in posts)
    current_hash = get_hash(all_text)

    found = []
    for post in posts:
        for kw in page['keywords']:
            if kw.lower() in post['text'].lower():
                snippet = get_snippet(post['text'], kw, 100)
                if snippet:
                    found.append((kw, snippet, post['link']))
                break

    if key not in state:
        state[key] = {'hash': current_hash, 'checked': datetime.now().isoformat()}
        msg = f"🚀 <b>ВК — первичное сканирование</b>\n📌 {page['section']}\n🔗 vk.com/{name}\n\n"
        if found:
            for kw, snippet, link in found[:3]:
                msg += f"🔑 <b>{kw}</b>\n<i>{snippet}</i>\n🔗 {link}\n\n"
            msg += f"💡 Возможно стоит обновить раздел <b>{page['section']}</b> на сайте."
        else:
            msg += f"ℹ️ Упоминаний не найдено в {len(posts)} постах."
        send_telegram(msg)
    elif current_hash != state[key].get('hash'):
        state[key] = {'hash': current_hash, 'checked': datetime.now().isoformat()}
        msg = f"🔔 <b>ВК — новый пост!</b>\n📌 {page['section']}\n🔗 vk.com/{name}\n\n"
        if found:
            for kw, snippet, link in found[:3]:
                msg += f"🔑 <b>{kw}</b>\n<i>{snippet}</i>\n🔗 {link}\n\n"
            msg += f"💡 Обнови раздел <b>{page['section']}</b> на a-pronin.ru"
        else:
            msg += "ℹ️ Новые посты без упоминаний."
        send_telegram(msg)
    else:
        state[key]['checked'] = datetime.now().isoformat()
        print(f"  Без изменений: vk.com/{name}")

# ─── Главная ───────────────────────────────────────────────

def run():
    print(f"Запуск: {datetime.now().strftime('%d.%m.%Y %H:%M')}")

    # Обрабатываем команды от пользователя
    print("Проверяю команды...")
    process_commands()

    # Мониторинг сайтов
    state = load_json(STATE_FILE, {})
    for page in WEB_PAGES:
        print(f"Сайт: {page['url']}")
        process_web(page, state)

    # Мониторинг ВКонтакте
    if VK_TOKEN:
        for page in VK_PAGES:
            print(f"ВК: vk.com/{page['screen_name']}")
            process_vk(page, state)
    else:
        print("VK_TOKEN не задан")

    save_json(STATE_FILE, state)
    print("Готово.")

if __name__ == '__main__':
    run()
