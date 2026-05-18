import requests
from bs4 import BeautifulSoup
import json
import os
import hashlib
from datetime import datetime

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')

PAGES = [
    # Консалтинг
    {'url': 'https://bitobe.ru/team/4573/', 'section': 'Консалтинг', 'keywords': ['Пронин', 'Александр']},
    {'url': 'https://vk.com/sansei4', 'section': 'Консалтинг/Дроны', 'keywords': ['Пронин', 'Александр', 'BITOBE', 'дрон', 'FPV', 'БПЛА']},
    {'url': 'https://vk.com/bitobe', 'section': 'Консалтинг', 'keywords': ['Пронин', 'Александр']},
    # Дроны
    {'url': 'https://fgd.spb.ru/', 'section': 'Дроны', 'keywords': ['Пронин', 'Александр']},
    {'url': 'https://vk.com/fgdrus', 'section': 'Дроны', 'keywords': ['Пронин', 'Александр']},
    {'url': 'https://vk.com/fgdpskov', 'section': 'Дроны', 'keywords': ['Пронин', 'Александр']},
    {'url': 'https://vk.com/fgdspb', 'section': 'Дроны', 'keywords': ['Пронин', 'Александр']},
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

def run():
    state = load_state()
    print(f"Запуск мониторинга: {datetime.now().strftime('%d.%m.%Y %H:%M')}")

    for page in PAGES:
        url = page['url']
        section = page['section']
        keywords = page['keywords']

        print(f"Проверяю: {url}")
        html = fetch_page(url)
        if not html:
            continue

        text = extract_text(html)
        current_hash = get_hash(text)
        found_kw = find_keywords(text, keywords)

        if url not in state:
            # Первый запуск — сохраняем базу
            state[url] = {'hash': current_hash, 'checked': datetime.now().isoformat()}
            msg = (
                f"🚀 <b>Первичное сканирование завершено</b>\n\n"
                f"📌 Раздел: {section}\n"
                f"🔗 {url}\n"
            )
            if found_kw:
                msg += f"✅ Найдены упоминания: <b>{', '.join(found_kw)}</b>\n"
                msg += f"\n💡 Возможно, стоит добавить это в раздел <b>{section}</b> на сайте."
            else:
                msg += "ℹ️ Упоминаний пока не найдено — база сохранена."
            send_telegram(msg)
            print(f"  → Первый запуск, база сохранена. Найдено: {found_kw}")

        else:
            prev_hash = state[url].get('hash')
            if current_hash != prev_hash:
                # Страница изменилась!
                state[url] = {'hash': current_hash, 'checked': datetime.now().isoformat()}
                msg = (
                    f"🔔 <b>Обнаружено изменение!</b>\n\n"
                    f"📌 Раздел: {section}\n"
                    f"🔗 {url}\n"
                )
                if found_kw:
                    msg += f"\n✅ Упоминания: <b>{', '.join(found_kw)}</b>"
                    msg += f"\n\n💡 Рекомендую обновить раздел <b>{section}</b> на сайте a-pronin.ru"
                else:
                    msg += "\nℹ️ Упоминаний твоего имени не найдено, но страница изменилась."
                send_telegram(msg)
                print(f"  → ИЗМЕНЕНИЕ! Ключевые слова: {found_kw}")
            else:
                state[url]['checked'] = datetime.now().isoformat()
                print(f"  → Без изменений")

    save_state(state)
    print("Готово.")

if __name__ == '__main__':
    run()
