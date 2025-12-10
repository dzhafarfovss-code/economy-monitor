import os
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup
import pdfplumber
import io
import re
import urllib3
from urllib.parse import urljoin
import json
import time

# --- НАСТРОЙКИ ---
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

class CBRAgent:
    def __init__(self):
        self.history_file = "history.json"
        self.processed_urls = self.load_history()
        
        # Настраиваем надежное соединение
        self.session = requests.Session()
        retries = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
        self.session.mount('https://', HTTPAdapter(max_retries=retries))
        
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }

        # СПИСОК ВАЖНЫХ ОТЧЕТОВ ЦБ
        self.targets = [
            r"Обзор рисков",               # Самое важное для валюты и нерезов
            r"Региональная экономика",     # Важно для ставки (кадры/зарплаты)
            r"Макроэкономический опрос",   # Ожидания рынка
            r"Денежно-кредитные условия",  # Ставки банков
            r"Мониторинг отраслевых",      # Потоки денег
            r"Доклад о денежно-кредитной", # Базовый сценарий
            r"Динамика потребительских цен", # Инфляция
            r"Инфляционные ожидания"       # Опросы населения
        ]

    def load_history(self):
        if os.path.exists(self.history_file):
            try:
                with open(self.history_file, 'r') as f:
                    return set(json.load(f))
            except:
                return set()
        return set()

    def save_history(self, url):
        self.processed_urls.add(url)
        with open(self.history_file, 'w') as f:
            json.dump(list(self.processed_urls), f)

    def send_telegram(self, message):
        if not TG_BOT_TOKEN or not TG_CHAT_ID:
            print("!!! Нет ключей Telegram")
            return

        print(f"📤 Отправка в TG: {message[:30]}...")
        # Разбиваем длинные сообщения
        for chunk in [message[i:i+4000] for i in range(0, len(message), 4000)]:
            url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
            data = {"chat_id": TG_CHAT_ID, "text": chunk, "parse_mode": "Markdown"}
            try:
                self.session.post(url, data=data, timeout=10)
                time.sleep(1)
            except Exception as e:
                print(f"Ошибка TG: {e}")

    def get_soup(self, url):
        try:
            resp = self.session.get(url, headers=self.headers, verify=False, timeout=30)
            resp.raise_for_status()
            return BeautifulSoup(resp.text, 'html.parser')
        except Exception as e:
            print(f"⚠️ Ошибка доступа к {url}: {e}")
            return None

    def extract_text_from_pdf(self, pdf_url):
        print(f"⬇️ Качаем PDF: {pdf_url}")
        try:
            resp = self.session.get(pdf_url, headers=self.headers, verify=False, timeout=60)
            with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
                text = ""
                # Читаем первые 6 страниц (самая суть всегда в начале)
                for i in range(min(6, len(pdf.pages))):
                    t = pdf.pages[i].extract_text()
                    if t: text += t + "\n"
                return text
        except Exception as e:
            print(f"Ошибка PDF: {e}")
            return None

    def analyze_with_gpt(self, text, title):
        if not OPENAI_API_KEY:
            return "⚠️ Нет ключа OpenAI. Текст:\n" + text[:500]

        print("🧠 GPT Анализирует...")
        try:
            from openai import OpenAI
            client = OpenAI(api_key=OPENAI_API_KEY)

            prompt = f"""
            Ты — циничный макроэкономист и трейдер. Твоя специализация — ОФЗ и Рубль.
            Проанализируй документ ЦБ РФ: "{title}".
            
            Дай четкий торговый сигнал. Не лей воду.
            
            СТРУКТУРА ОТВЕТА:
            1. 🦅 **Риторика:** (Жесткая / Нейтральная / Мягкая). Почему? (1 предложение).
            2. 📊 **Ключевые данные:** (Инфляция, Инфляционные ожидания, Рынок труда/Кадры, Кредитование).
            3. 🏛 **Влияние на ОФЗ:** (Покупать / Продавать / Держать / Внимание на флоатеры).
            4. 🔥 **Риск:** Самая главная проблема, описанная в отчете.

            Текст документа (начало):
            {text[:12000]}
            """

            response = client.chat.completions.create(
                model="gpt-4o", 
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3
            )
            return response.choices[0].message.content
        except Exception as e:
            return f"Ошибка GPT: {e}"

    def run(self):
        print("🔍 Начинаем сканирование ЦБ РФ...")
        
        # Проверяем Календарь и основные разделы аналитики
        urls_to_check = [
            "https://www.cbr.ru/calendar",
            "https://www.cbr.ru/analytics/dkp/",
            "https://www.cbr.ru/analytics/fin_stab/"
        ]
        
        found_count = 0

        for start_url in urls_to_check:
            soup = self.get_soup(start_url)
            if not soup: continue

            links = soup.find_all('a')
            for link in links:
                title = link.get_text(strip=True)
                href = link.get('href')
                
                if not href or not title: continue
                
                # === ФИЛЬТР: ТОЛЬКО 2025 ГОД ===
                # Отсекаем все старые отчеты, чтобы не спамить
                if "2025" not in title and "2025" not in href:
                    continue

                # Проверяем, подходит ли название под наш список интересов
                is_target = any(re.search(p, title, re.IGNORECASE) for p in self.targets)
                
                if is_target:
                    full_url = urljoin("https://www.cbr.ru", href)
                    
                    # Если уже обрабатывали - пропускаем
                    if full_url in self.processed_urls:
                        continue
                    
                    print(f"🔥 НАЙДЕН НОВЫЙ ОТЧЕТ: {title}")
                    
                    # Ищем PDF
                    pdf_url = None
                    if href.lower().endswith('.pdf'):
                        pdf_url = full_url
                    else:
                        # Заходим внутрь страницы
                        sub_soup = self.get_soup(full_url)
                        if sub_soup:
                            # Ищем ссылку на скачивание
                            pl = sub_soup.find('a', href=re.compile(r'\.pdf$', re.IGNORECASE))
                            if pl: pdf_url = urljoin("https://www.cbr.ru", pl['href'])
                    
                    if pdf_url:
                        text = self.extract_text_from_pdf(pdf_url)
                        if text:
                            analysis = self.analyze_with_gpt(text, title)
                            
                            # Формируем сообщение
                            msg = f"🏦 **ЦБ РФ: ВЫШЕЛ ОТЧЕТ**\n\n📄 *{title}*\n\n{analysis}\n\n🔗 [Читать оригинал]({pdf_url})"
                            
                            self.send_telegram(msg)
                            self.save_history(full_url)
                            found_count += 1
                    else:
                        print(f"PDF не найден для {title}")

        print(f"✅ Готово. Найдено новых отчетов: {found_count}")

if __name__ == "__main__":
    CBRAgent().run()
