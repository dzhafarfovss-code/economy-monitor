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
        
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        }

        self.targets = [
            "Обзор рисков",
            "Региональная экономика",
            "Макроэкономический опрос",
            "Денежно-кредитные условия",
            "Мониторинг отраслевых",
            "Доклад о денежно-кредитной",
            "Инфляционные ожидания"
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
        
        url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
        
        for chunk in [message[i:i+4000] for i in range(0, len(message), 4000)]:
            # 1. Пробуем Markdown
            data = {"chat_id": TG_CHAT_ID, "text": chunk, "parse_mode": "Markdown"}
            resp = self.session.post(url, data=data)
            
            # 2. Если ошибка (400), шлем чистый текст
            if resp.status_code != 200:
                print("⚠️ Ошибка формата. Шлем обычным текстом.")
                clean_text = chunk.replace("*", "").replace("_", "").replace("`", "")
                self.session.post(url, data={"chat_id": TG_CHAT_ID, "text": clean_text})
            
            time.sleep(1)

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
        if not OPENAI_API_KEY: return "⚠️ Нет ключа AI."
        print("🧠 GPT Анализ...")
        try:
            from openai import OpenAI
            client = OpenAI(api_key=OPENAI_API_KEY)
            prompt = f"""
            Ты — макроэкономист. Проанализируй отчет ЦБ: "{title}".
            Дай сигнал для ОФЗ. 
            НЕ используй markdown символы в тексте, кроме заголовков.
            
            СТРУКТУРА:
            1. *Риторика:* (Жесткая/Мягкая).
            2. *Факты:* (Инфляция, Кредиты).
            3. *Вывод ОФЗ:* (Покупать/Продавать).
            4. *Риск:* Главная угроза.

            Текст: {text[:12000]}
            """
            response = client.chat.completions.create(
                model="gpt-4o", messages=[{"role": "user", "content": prompt}], temperature=0.3
            )
            return response.choices[0].message.content
        except Exception as e:
            return f"GPT Error: {e}"

    def run(self):
        print("🔍 Сканирование ЦБ...")
        url = "https://www.cbr.ru/calendar"
        soup = self.get_soup(url)
        if not soup: return

        # Локальный кеш, чтобы не слать один и тот же файл 10 раз за один запуск
        session_pdfs = set()

        links = soup.find_all('a')
        for link in links:
            title = link.get_text(strip=True)
            href = link.get('href')
            if not href or not title: continue
            
            is_target = any(re.search(p, title, re.IGNORECASE) for p in self.targets)
            
            if is_target:
                full_url = urljoin("https://www.cbr.ru", href)
                sub_soup = self.get_soup(full_url)
                
                if sub_soup:
                    pdf_links = sub_soup.find_all('a', href=re.compile(r'\.pdf$', re.IGNORECASE))
                    found_pdf = None
                    
                    # 1. Ищем Ноябрь (11)
                    for pl in pdf_links:
                        if "2025" in pl['href'] and ("-11" in pl['href'] or "_11" in pl['href']):
                            found_pdf = urljoin("https://www.cbr.ru", pl['href'])
                            break
                    
                    # 2. Если нет, берем Октябрь (10)
                    if not found_pdf:
                        for pl in pdf_links:
                            if "2025" in pl['href'] and ("-10" in pl['href'] or "_10" in pl['href']):
                                found_pdf = urljoin("https://www.cbr.ru", pl['href'])
                                break
                    
                    if found_pdf:
                        # ПРОВЕРКА НА ДУБЛИКАТЫ (САМОЕ ВАЖНОЕ)
                        if found_pdf in self.processed_urls:
                            # Мы это уже отправляли в прошлом запуске
                            continue
                        
                        if found_pdf in session_pdfs:
                            # Мы это уже нашли 5 секунд назад в этом же запуске
                            continue
                        
                        # Добавляем в текущий список, чтобы не обрабатывать снова
                        session_pdfs.add(found_pdf)
                        
                        print(f"🔥 НОВЫЙ ФАЙЛ: {found_pdf}")
                        text = self.extract_text_from_pdf(found_pdf)
                        if text:
                            ans = self.analyze_with_gpt(text, title)
                            self.send_telegram(f"🏦 **ЦБ РФ**\n\n📄 {title}\n\n{ans}\n🔗 {found_pdf}")
                            self.save_history(found_pdf) # Сохраняем в вечную память
                            time.sleep(3)

        print("✅ Готово.")

if __name__ == "__main__":
    CBRAgent().run()
