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

    def send_telegram(self, message):
        if not TG_BOT_TOKEN or not TG_CHAT_ID: return
        
        url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
        
        # Разбиваем длинное сообщение
        for chunk in [message[i:i+4000] for i in range(0, len(message), 4000)]:
            # ПОПЫТКА 1: Красиво (Markdown)
            data = {"chat_id": TG_CHAT_ID, "text": chunk, "parse_mode": "Markdown"}
            resp = self.session.post(url, data=data)
            
            # Если ошибка форматирования (400 Bad Request)
            if resp.status_code != 200:
                print(f"⚠️ Ошибка Markdown: {resp.text}. Пробую обычным текстом...")
                # ПОПЫТКА 2: Обычный текст (Без форматирования)
                clean_text = chunk.replace("*", "").replace("_", "").replace("`", "")
                data = {"chat_id": TG_CHAT_ID, "text": clean_text} # Без parse_mode
                self.session.post(url, data=data)
            else:
                print("✅ Сообщение доставлено.")
            
            time.sleep(1)

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
            # Просим GPT не использовать сложные символы Markdown
            prompt = f"""
            Ты — макроэкономист. Проанализируй отчет ЦБ: "{title}".
            Дай сигнал для ОФЗ. Используй минимум форматирования (только звездочки для жирного).
            
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
        print("🔍 Поиск в Календаре ЦБ...")
        url = "https://www.cbr.ru/calendar"
        
        soup = self.get_soup(url)
        if not soup: return

        links = soup.find_all('a')
        for link in links:
            title = link.get_text(strip=True)
            href = link.get('href')
            
            if not href or not title: continue
            
            # Проверяем название
            is_target = any(re.search(p, title, re.IGNORECASE) for p in self.targets)
            
            if is_target:
                full_url = urljoin("https://www.cbr.ru", href)
                print(f"🔎 Раздел найден: {title}")
                
                sub_soup = self.get_soup(full_url)
                if sub_soup:
                    # Собираем ВСЕ PDF со страницы
                    pdf_links = sub_soup.find_all('a', href=re.compile(r'\.pdf$', re.IGNORECASE))
                    
                    found_pdf_url = None
                    
                    # ПРИОРИТЕТ 1: Ищем Ноябрь 2025 (11-2025, 2025-11, 11_25)
                    for pl in pdf_links:
                        ref = pl['href']
                        if "2025" in ref and ("-11" in ref or "_11" in ref or "11_2025" in ref):
                            found_pdf_url = urljoin("https://www.cbr.ru", ref)
                            print("🔥 НАЙДЕН НОЯБРЬСКИЙ ОТЧЕТ!")
                            break
                    
                    # ПРИОРИТЕТ 2: Если ноября нет, берем Октябрь (10)
                    if not found_pdf_url:
                        for pl in pdf_links:
                            ref = pl['href']
                            if "2025" in ref and ("-10" in ref or "_10" in ref or "10_2025" in ref):
                                found_pdf_url = urljoin("https://www.cbr.ru", ref)
                                print("ℹ️ Ноября нет, берем Октябрь.")
                                break
                    
                    if found_pdf_url:
                        text = self.extract_text_from_pdf(found_pdf_url)
                        if text:
                            ans = self.analyze_with_gpt(text, title)
                            # Отправляем! (Функция сама разберется с форматом)
                            self.send_telegram(f"🏦 **ЦБ РФ**\n\n📄 {title}\n\n{ans}\n🔗 {found_pdf_url}")
                            # Делаем паузу и выходим (чтобы не слать дубли одной новости)
                            time.sleep(2)

        print("✅ Готово.")

if __name__ == "__main__":
    CBRAgent().run()
