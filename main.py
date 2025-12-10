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
        
        # 🔥 РЕЖИМ АМНЕЗИИ: Мы специально не загружаем историю, 
        # чтобы он прочитал существующие отчеты заново.
        self.processed_urls = set() 
        
        self.session = requests.Session()
        retries = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
        self.session.mount('https://', HTTPAdapter(max_retries=retries))
        
        self.headers = {
            "User-Agent": "Mozilla/5.0 (compatible; YandexBot/3.0; +http://yandex.com/bots)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        }

        # ДОБАВИЛ "ТРЕНДЫ" В СПИСОК
        self.targets = [
            r"О чем говорят тренды",        # <--- ДОБАВИЛИ ЭТО
            r"Обзор рисков",
            r"Региональная экономика",
            r"Макроэкономический опрос",
            r"Денежно-кредитные условия",
            r"Мониторинг отраслевых",
            r"Доклад о денежно-кредитной",
            r"Инфляционные ожидания",
            r"Динамика потребительских цен"
        ]

    def save_history(self, url):
        # В этом режиме можно не сохранять, или сохранять - как хочешь.
        # Пока сохраняем, чтобы при следующем запуске (через час) не спамил.
        self.processed_urls.add(url)
        # Если файла нет, создаем
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
            return BeautifulSoup(resp.text, 'html.parser')
        except:
            return None

    def extract_text_from_pdf(self, pdf_url):
        print(f"⬇️ Качаем: {pdf_url}")
        try:
            resp = self.session.get(pdf_url, headers=self.headers, verify=False, timeout=60)
            with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
                text = ""
                for i in range(min(7, len(pdf.pages))):
                    t = pdf.pages[i].extract_text()
                    if t: text += t + "\n"
                return text
        except:
            return None

    def analyze_with_gpt(self, text, title):
        if not OPENAI_API_KEY: return "⚠️ Нет ключа OpenAI."
        print("🧠 GPT Анализ...")
        try:
            from openai import OpenAI
            client = OpenAI(api_key=OPENAI_API_KEY)
            prompt = f"""
            Ты — макроэкономист. 
            Проанализируй документ ЦБ РФ: "{title}".
            Дай сигнал для ОФЗ.
            
            СТРУКТУРА:
            1. 🦅 **Риторика:** (Жесткая/Мягкая).
            2. 📊 **Факты:** (Инфляция, Ожидания, Кредиты).
            3. 🏛 **Вывод для ОФЗ:** (Покупать/Продавать).
            4. 🔥 **Риск:** (Кратко).

            Текст: {text[:12000]}
            """
            response = client.chat.completions.create(
                model="gpt-4o", messages=[{"role": "user", "content": prompt}], temperature=0.3
            )
            return response.choices[0].message.content
        except Exception as e:
            return f"GPT Error: {e}"

    def run(self):
        print("🔍 Принудительный поиск в Календаре ЦБ...")
        url = "https://www.cbr.ru/calendar"
        
        soup = self.get_soup(url)
        if soup:
            links = soup.find_all('a')
            for link in links:
                title = link.get_text(strip=True)
                href = link.get('href')
                if not href or not title: continue
                
                # Фильтр: 2025 год
                if "2025" not in title and "2025" not in href: continue

                # Ищем по расширенному списку
                is_target = any(re.search(p, title, re.IGNORECASE) for p in self.targets)
                
                if is_target:
                    full_url = urljoin("https://www.cbr.ru", href)
                    print(f"🔥 НАШЕЛ: {title}")
                    
                    # Логика PDF
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
        print("✅ Готово.")

if __name__ == "__main__":
    CBRAgent().run()
