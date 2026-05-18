import requests
from bs4 import BeautifulSoup
import json
import os
import hashlib
from datetime import datetime

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')
VK_TOKEN = os.environ.get('VK_TOKEN')

WEB_PAGES = [
    {'url': 'https://bitobe.ru/team/4573/', 'section': 'Консалтинг', 'keywords': ['Пронин', 'Александр']},
    {'url': 'https://fgd.spb.ru/', 'section': 'Дроны', 'keywords': ['Пронин', 'Александр']},
]

VK_PAGES = [
    {'screen_name': 'sansei4', 'section': 'Консалтинг/Дроны', 'keywords': ['Пронин', 'Александр', 'BITOBE', 'дрон', 'FPV', 'БПЛА']},
    {'screen_name': 'bitobe', 'section': 'Консалтинг', 'keywords': ['Пронин', 'Александр']},
    {'screen_name': 'fgdrus', 'section': 'Дроны', 'keywords': ['Пронин', 'Александр']},
    {'screen_name': 'fgdpskov', 'section': 'Дроны', 'keywords': ['Пронин', 'Александр']},
    {'screen_name': 'fgdspb', 'section': 'Дроны', 'keywords': ['Пронин', 'Александр']},
]

STATE_FILE = 'data/state.json'

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_state(state):
    os.makedirs('data', exist_ok=True)
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def fetch_page(url):
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    try:
        r = requests.get(url, headers=headers, timeout=15)
        r.encoding = 'utf-8'
        return r.text
    except Exception as e:
        print(f"Ошибка загрузки {url}: {e}")
        return None

def extract_text(html):
    soup = BeautifulSoup(html, 'html.parser')
    for tag in soup(['script', 'style']):
        tag.decompose()
    return soup.get_text(separator=' ', strip=True)

def get_hash(text):
    return hashlib.md5(text.encode('utf-8')).hexdigest()

def send_telegram(message):
    url = f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage'
    try:
        resp = requests.post(url, data={
            'chat_id': CHAT_ID,
            'text': message,
            'parse_mode': 'HTML',
            'disable_web_page_preview': False
        })
        print(f"  Telegram: {resp.status_code}")
    except Exception as e:
        print(f"Ошибка Telegram: {e}")

def get_snippet(text, keyword, context=120):
    idx = text.lower().find(keyword.lower())
    if idx == -1:
        return None
    start = max(0, idx - context)
    end = min(len(text), idx + len(keyword) + context)
    snippet = ' '.join(text[start:end].split())
    if start > 0:
        snippet = '...' + snippet
    if end < len(text):
        snippet += '...'
    return snippet

def get_vk_data(screen_name):
    """Возвращает список постов с текстом, id и owner_id"""
    try:
        # Определяем тип и ID
        r = requests.get('https://api.vk.com/method/utils.resolveScreenName', params={
            'screen_name': screen_name,
            'access_token': VK_TOKEN,
            'v': '5.131'
        }, timeout=10)
        data = r.json()
        print(f"  resolveScreenName для {screen_name}: {data}")

        if 'error' in data or not data.get('response'):
            return None

        obj = data['response']
        obj_type = obj['type']
        owner_id = obj['object_id']
        if obj_type in ('group', 'page'):
            owner_id = -owner_id

        # Получаем посты
        r2 = requests.get('https://api.vk.com/method/wall.get', params={
            'owner_id': owner_id,
            'count': 50,
            'filter': 'all',
            'access_token': VK_TOKEN,
            'v': '5.131'
        }, timeout=10)
        wall = r2.json()

        if 'error' in wall:
            print(f"  wall.get ошибка: {wall['error']}")
            return None

        posts = wall.get('response', {}).get('items', [])
        print(f"  Получено постов: {len(posts)}")

        result = []
        for post in posts:
            text = post.get('text', '').strip()
            post_id = post.get('id')
            if text and post_id:
                link = f"https://vk.com/wall{owner_id}_{post_id}"
                result.append({'text': text, 'link': link, 'id': post_id})

        return result, owner_id

    except Exception as e:
        print(f"  Ошибка ВК для {screen_name}: {e}")
        return None

def process_web(page, state):
    url = page['url']
    section = page['section']
    keywords = page['keywords']

    html = fetch_page(url)
    if not html:
        return

    text = extract_text(html)
    current_hash = get_hash(text)

    # Ищем все упоминания с фрагментами
    found = []
    for kw in keywords:
        snippet = get_snippet(text, kw)
        if snippet:
            found.append((kw, snippet, url))

    if url not in state:
        state[url] = {'hash': current_hash, 'checked': datetime.now().isoformat()}
        msg = f"🚀 <b>Первичное сканирование</b>\n📌 {section}\n\n"
        if found:
            for kw, snippet, link in found[:3]:
                msg += f"🔑 <b>{kw}</b>\n<i>{snippet}</i>\n🔗 {link}\n\n"
            msg += f"💡 Возможно стоит обновить раздел <b>{section}</b> на сайте."
        else:
            msg += f"🔗 {url}\nℹ️ Упоминаний не найдено — база сохранена."
        send_telegram(msg)
    elif current_hash != state[url].get('hash'):
        state[url] = {'hash': current_hash, 'checked': datetime.now().isoformat()}
        msg = f"🔔 <b>Изменение!</b>\n📌 {section}\n\n"
        if found:
            for kw, snippet, link in found[:3]:
                msg += f"🔑 <b>{kw}</b>\n<i>{snippet}</i>\n🔗 {link}\n\n"
            msg += f"💡 Обнови раздел <b>{section}</b> на a-pronin.ru"
        else:
            msg += f"🔗 {url}\nℹ️ Страница изменилась, упоминаний не найдено."
        send_telegram(msg)
    else:
        state[url]['checked'] = datetime.now().isoformat()
        print(f"  → Без изменений: {url}")

def process_vk(page, state):
    name = page['screen_name']
    section = page['section']
    keywords = page['keywords']
    key = f"vk_{name}"

    result = get_vk_data(name)
    if result is None:
        print(f"  → ВК: нет данных для {name}")
        return

    posts, owner_id = result
    if not posts:
        print(f"  → ВК: нет текстовых постов для {name}")
        return

    # Общий текст для хеша
    all_text = '\n'.join(p['text'] for p in posts)
    current_hash = get_hash(all_text)

    # Ищем упоминания с прямыми ссылками на посты
    found = []
    for post in posts:
        for kw in keywords:
            if kw.lower() in post['text'].lower():
                snippet = get_snippet(post['text'], kw, context=100)
                if snippet:
                    found.append((kw, snippet, post['link']))
                break  # один раз на пост

    if key not in state:
        state[key] = {'hash': current_hash, 'checked': datetime.now().isoformat()}
        msg = f"🚀 <b>ВКонтакте — первичное сканирование</b>\n📌 {section}\n🔗 vk.com/{name}\n\n"
        if found:
            for kw, snippet, link in found[:3]:
                msg += f"🔑 <b>{kw}</b>\n<i>{snippet}</i>\n🔗 {link}\n\n"
            msg += f"💡 Возможно стоит обновить раздел <b>{section}</b> на сайте."
        else:
            msg += f"ℹ️ Упоминаний не найдено в {len(posts)} постах — база сохранена."
        send_telegram(msg)
    elif current_hash != state[key].get('hash'):
        state[key] = {'hash': current_hash, 'checked': datetime.now().isoformat()}
        msg = f"🔔 <b>ВКонтакте — новый пост!</b>\n📌 {section}\n🔗 vk.com/{name}\n\n"
        if found:
            for kw, snippet, link in found[:3]:
                msg += f"🔑 <b>{kw}</b>\n<i>{snippet}</i>\n🔗 {link}\n\n"
            msg += f"💡 Обнови раздел <b>{section}</b> на a-pronin.ru"
        else:
            msg += "ℹ️ Новые посты без упоминаний твоего имени."
        send_telegram(msg)
    else:
        state[key]['checked'] = datetime.now().isoformat()
        print(f"  → Без изменений: vk.com/{name}")

def run():
    state = load_state()
    print(f"Запуск: {datetime.now().strftime('%d.%m.%Y %H:%M')}")

    for page in WEB_PAGES:
        print(f"Сайт: {page['url']}")
        process_web(page, state)

    if VK_TOKEN:
        for page in VK_PAGES:
            print(f"ВКонтакте: vk.com/{page['screen_name']}")
            process_vk(page, state)
    else:
        print("VK_TOKEN не задан")

    save_state(state)
    print("Готово.")

if __name__ == '__main__':
    run()
