import requests
from bs4 import BeautifulSoup
import json
import os
import hashlib
from datetime import datetime

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')
VK_TOKEN = os.environ.get('VK_TOKEN')

# Обычные сайты
WEB_PAGES = [
    {'url': 'https://bitobe.ru/team/4573/', 'section': 'Консалтинг', 'keywords': ['Пронин', 'Александр']},
    {'url': 'https://fgd.spb.ru/', 'section': 'Дроны', 'keywords': ['Пронин', 'Александр']},
]

# ВКонтакте страницы (через API)
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
        requests.post(url, data={'chat_id': CHAT_ID, 'text': message, 'parse_mode': 'HTML'})
    except Exception as e:
        print(f"Ошибка отправки в Telegram: {e}")

def find_keywords(text, keywords):
    return [kw for kw in keywords if kw.lower() in text.lower()]

def get_vk_wall(screen_name):
    try:
        r = requests.get('https://api.vk.com/method/utils.resolveScreenName', params={
            'screen_name': screen_name,
            'access_token': VK_TOKEN,
            'v': '5.199'
        }, timeout=10)
        data = r.json()
        if 'error' in data or not data.get('response'):
            print(f"  ВК: не удалось найти {screen_name}")
            return None
        obj = data['response']
        owner_id = obj['object_id']
        if obj['type'] == 'group':
            owner_id = -owner_id
        r2 = requests.get('https://api.vk.com/method/wall.get', params={
            'owner_id': owner_id,
            'count': 20,
            'access_token': VK_TOKEN,
            'v': '5.199'
        }, timeout=10)
        wall = r2.json()
        if 'error' in wall:
            print(f"  ВК ошибка для {screen_name}: {wall['error']}")
            return None
        posts = wall.get('response', {}).get('items', [])
        return '\n'.join(p.get('text', '') for p in posts if p.get('text'))
    except Exception as e:
        print(f"  Ошибка ВК для {screen_name}: {e}")
        return None

def process(key, content, section, keywords, url):
    state = load_state()
    current_hash = get_hash(content)
    found_kw = find_keywords(content, keywords)

    if key not in state:
        state[key] = {'hash': current_hash, 'checked': datetime.now().isoformat()}
        msg = f"🚀 <b>Первичное сканирование завершено</b>\n\n📌 Раздел: {section}\n🔗 {url}\n"
        if found_kw:
            msg += f"✅ Найдены упоминания: <b>{', '.join(found_kw)}</b>\n"
            msg += f"\n💡 Возможно, стоит добавить это в раздел <b>{section}</b> на сайте."
        else:
            msg += "ℹ️ Упоминаний пока не найдено — база сохранена."
        send_telegram(msg)
        print(f"  → Первый запуск. Найдено: {found_kw}")
    else:
        if current_hash != state[key].get('hash'):
            state[key] = {'hash': current_hash, 'checked': datetime.now().isoformat()}
            msg = f"🔔 <b>Обнаружено изменение!</b>\n\n📌 Раздел: {section}\n🔗 {url}\n"
            if found_kw:
                msg += f"\n✅ Упоминания: <b>{', '.join(found_kw)}</b>"
                msg += f"\n\n💡 Рекомендую обновить раздел <b>{section}</b> на сайте a-pronin.ru"
            else:
                msg += "\nℹ️ Страница изменилась, упоминаний имени не найдено."
            send_telegram(msg)
            print(f"  → ИЗМЕНЕНИЕ! {found_kw}")
        else:
            state[key]['checked'] = datetime.now().isoformat()
            print(f"  → Без изменений")
    save_state(state)

def run():
    print(f"Запуск: {datetime.now().strftime('%d.%m.%Y %H:%M')}")

    for page in WEB_PAGES:
        url = page['url']
        print(f"Сайт: {url}")
        html = fetch_page(url)
        if html:
            process(url, extract_text(html), page['section'], page['keywords'], url)

    if VK_TOKEN:
        for page in VK_PAGES:
            name = page['screen_name']
            url = f"https://vk.com/{name}"
            print(f"ВКонтакте: {url}")
            content = get_vk_wall(name)
            if content:
                process(f"vk_{name}", content, page['section'], page['keywords'], url)
            else:
                print(f"  → Нет данных")
    else:
        print("VK_TOKEN не задан")

    print("Готово.")

if __name__ == '__main__':
    run()
