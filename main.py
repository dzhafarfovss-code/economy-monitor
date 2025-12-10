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
import datetime

# --- НАСТРОЙКИ ---
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

class CBRAgent:
    def __init__(self):
        # ИСТОРИЮ ОТКЛЮЧАЕМ, чтобы он точно прислал (даже если уже видел)
        self.processed_urls = set()
        
        self.session = requests.Session()
        retries = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
        self.session.mount('https://', HTTPAdapter(max_retries=retries))
        
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        }

        # СПИСОК РАЗДЕЛОВ
        self.targets = [
            r"О чем говорят тренды",
            r"Обзор рисков",
            r"Региональная экономика",
            r"Макроэкономический опрос",
            r"Денежно-кредитные условия",
            r"Мониторинг отраслевых",
            r"Доклад о денежно-кредитной",
            r"Инфляционные ожидания"
        ]

        # 🔥 ГЛАВНОЕ: ПАТТЕРНЫ ДЛЯ ПОИСКА ФАЙЛОВ
        # Мы ищем файлы, в названии которых есть 10-й или 11-й месяц 2025 года
        self.target_files = [
            "2025-10", "2025_10", # Октябрь
            "2025-11", "2025_11", # Ноябрь
            "10_2025", "11_2025"  # На всякий случай другой формат
        ]

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
        except Exception as e:
            print(f"Ошибка доступа {url}: {e}")
            return None

    def extract_text_from_pdf(self, pdf_url):
        print(f"⬇️ Качаем PDF: {pdf_url}")
        try:
            resp = self.session.get(pdf_url, headers=self.headers, verify=False, timeout=60)
            with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
                text = ""
                # Читаем 7 страниц
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
            Ты — макроэкономист. Проанализируй отчет ЦБ РФ: "{title}".
            Дай СИГНАЛ для трейдера ОФЗ.
            
            СТРУКТУРА:
            1. 🦅 **Риторика:** (Жесткая/Мягкая) - аргументируй.
            2. 📊 **Факты:** (Инфляция, Кредитование, Ожидания).
            3. 🏛 **Вывод для ОФЗ:** (Покупать/Продавать/Держать).
            4. 🔥 **Инсайт:** Самое важное из отчета.

            Текст: {text[:12000]}
            """
            response = client.chat.completions.create(
                model="gpt-4o", messages=[{"role": "user", "content": prompt}], temperature=0.4
            )
            return response.choices[0].message.content
        except Exception as e:
            return f"GPT Error: {e}"

    def run(self):
        print("🔍 Сканируем Календарь ЦБ...")
        url = "https://www.cbr.ru/calendar"
        
        soup = self.get_soup(url)
        if not soup: return

        processed_pdfs = set() # Чтобы не слать один и тот же файл дважды за запуск

        links = soup.find_all('a')
        for link in links:
            title = link.get_text(strip=True)
            href = link.get('href')
            
            if not href or not title: continue
            
            # Проверяем название раздела (например "Обзор рисков")
            is_target = any(re.search(p, title, re.IGNORECASE) for p in self.targets)
            
            if is_target:
                full_url = urljoin("https://www.cbr.ru", href)
                print(f"🔎 Заходим в раздел: {title}")
                
                # Заходим внутрь страницы раздела
                sub_soup = self.get_soup(full_url)
                if sub_soup:
                    # Ищем ВСЕ ссылки на PDF
                    pdf_links = sub_soup.find_all('a', href=re.compile(r'\.pdf$', re.IGNORECASE))
                    
                    for pl in pdf_links:
                        pdf_href = pl['href']
                        
                        # === ФИЛЬТР: Ищем Октябрь (10) и Ноябрь (11) 2025 ===
                        is_relevant_file = any(pattern in pdf_href for pattern in self.target_files)
                        
                        if is_relevant_file:
                            target_pdf = urljoin("https://www.cbr.ru", pdf_href)
                            
                            # Проверка дублей
                            if target_pdf in processed_pdfs: continue
                            processed_pdfs.add(target_pdf)
                            
                            print(f"🔥 НАЙДЕН НУЖНЫЙ PDF: {target_pdf}")
                            
                            text = self.extract_text_from_pdf(target_pdf)
                            if text:
                                ans = self.analyze_with_gpt(text, title)
                                self.send_telegram(f"🏦 **ЦБ РФ**\n\n📄 {title}\n\n{ans}\n🔗 {target_pdf}")
                                # Небольшая пауза перед следующим файлом
                                time.sleep(3)

        print("✅ Готово.")

if __name__ == "__main__":
    CBRAgent().run()
