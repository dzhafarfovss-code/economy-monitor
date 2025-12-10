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
        
        self.session = requests.Session()
        retries = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
        self.session.mount('https://', HTTPAdapter(max_retries=retries))
        
        # Притворяемся Яндексом (на всякий случай)
        self.headers = {
            "User-Agent": "Mozilla/5.0 (compatible; YandexBot/3.0; +http://yandex.com/bots)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        }

        # СПИСОК ВАЖНЫХ ОТЧЕТОВ
        self.targets = [
            r"Обзор рисков",
            r"Региональная экономика",
            r"Макроэкономический опрос",
            r"Денежно-кредитные условия",
            r"Мониторинг отраслевых",
            r"Доклад о денежно-кредитной",
            r"Инфляционные ожидания",
            r"Динамика потребительских цен"
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
        if not TG_BOT_TOKEN or not TG_CHAT_ID: return
        print(f"📤 TG: {message[:30]}...")
        for chunk in [message[i:i+4000] for i in range(0, len(message), 4000)]:
            url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
            data = {"chat_id": TG_CHAT_ID, "text": chunk, "parse_mode": "Markdown"}
            try:
                self.session.post(url, data=data, timeout=10)
                time.sleep(1)
            except Exception as e:
                print(f"TG Error: {e}")

    def get_soup(self, url):
        try:
            resp = self.session.get(url, headers=self.headers, verify=False, timeout=30)
            resp.raise_for_status()
            return BeautifulSoup(resp.text, 'html.parser')
        except Exception as e:
            print(f"⚠️ Ошибка доступа ({url}): {e}")
            return None

    def extract_text_from_pdf(self, pdf_url):
        print(f"⬇️ Качаем PDF: {pdf_url}")
        try:
            resp = self.session.get(pdf_url, headers=self.headers, verify=False, timeout=60)
            with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
                text = ""
                # Первые 7 страниц
                for i in range(min(7, len(pdf.pages))):
                    t = pdf.pages[i].extract_text()
                    if t: text += t + "\n"
                return text
        except Exception as e:
            print(f"Ошибка PDF: {e}")
            return None

    def analyze_with_gpt(self, text, title):
        if not OPENAI_API_KEY: return "⚠️ Нет ключа OpenAI."
        print("🧠 GPT Анализ...")
        try:
            from openai import OpenAI
            client = OpenAI(api_key=OPENAI_API_KEY)
            prompt = f"""
            Ты — макроэкономист-трейдер. 
            Проанализируй документ ЦБ РФ: "{title}".
            Дай сигнал для ОФЗ.
            
            СТРУКТУРА:
            1. 🦅 **Риторика:** (Жесткая/Мягкая/Нейтральная).
            2. 📊 **Факты:** (Инфляция, Ожидания, Кредиты).
            3. 🏛 **Вывод для ОФЗ:** (Покупать/Продавать/Держать).
            4. 🔥 **Риск:** (Главная угроза).

            Текст: {text[:12000]}
            """
            response = client.chat.completions.create(
                model="gpt-4o", messages=[{"role": "user", "content": prompt}], temperature=0.3
            )
            return response.choices[0].message.content
        except Exception as e:
            return f"GPT Error: {e}"

    def run(self):
        print("🔍 Сканируем Календарь ЦБ...")
        
        # ТОЛЬКО ОДНА ССЫЛКА - КАЛЕНДАРЬ
        url = "https://www.cbr.ru/calendar"
        
        soup = self.get_soup(url)
        found_new = False

        if soup:
            links = soup.find_all('a')
            for link in links:
                title = link.get_text(strip=True)
                href = link.get('href')
                
                if not href or not title: continue
                
                # 1. ФИЛЬТР 2025 (Чтобы не брать старье)
                if "2025" not in title and "2025" not in href:
                    continue

                # 2. ФИЛЬТР ПО НАЗВАНИЮ
                is_target = any(re.search(p, title, re.IGNORECASE) for p in self.targets)
                
                if is_target:
                    full_url = urljoin("https://www.cbr.ru", href)
                    
                    # Проверка истории
                    if full_url in self.processed_urls:
                        continue
                    
                    print(f"🔥 НАЙДЕН НОВЫЙ: {title}")
                    found_new = True
                    
                    # Ищем PDF
                    pdf_url = full_url if href.lower().endswith('.pdf') else None
                    if not pdf_url:
                        sub = self.get_soup(full_url)
                        if sub:
                            pl = sub.find('a', href=re.compile(r'\.pdf$', re.IGNORECASE))
                            if pl: pdf_url = urljoin("https://www.cbr.ru", pl['href'])
                    
                    if pdf_url:
                        text = self.extract_text_from_pdf(pdf_url)
                        if text:
                            ans = self.analyze_with_gpt(text, title)
                            self.send_telegram(f"🏦 **ЦБ РФ**\n\n📄 {title}\n\n{ans}\n🔗 {pdf_url}")
                            self.save_history(full_url)
                    else:
                        print("PDF не найден.")

        if not found_new:
            print("✅ Новых документов в календаре пока нет.")
        else:
            print("✅ Уведомления отправлены.")

if __name__ == "__main__":
    CBRAgent().run()
