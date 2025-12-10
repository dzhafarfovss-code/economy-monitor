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
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        }

        # Цели (упростил, ищем самое важное)
        self.targets = [
            "Обзор рисков",
            "Региональная экономика",
            "Макроэкономический опрос"
        ]

    def send_telegram(self, message):
        if not TG_BOT_TOKEN or not TG_CHAT_ID: return
        print(f"📤 TG Out: {message[:50]}...")
        url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
        try:
            self.session.post(url, data={"chat_id": TG_CHAT_ID, "text": message, "parse_mode": "Markdown"}, timeout=10)
        except Exception as e:
            print(f"TG Error: {e}")

    def get_soup(self, url):
        try:
            resp = self.session.get(url, headers=self.headers, verify=False, timeout=30)
            return BeautifulSoup(resp.text, 'html.parser')
        except Exception as e:
            print(f"❌ Ошибка доступа {url}: {e}")
            return None

    def extract_text_from_pdf(self, pdf_url):
        print(f"⬇️ Качаем PDF: {pdf_url}")
        try:
            resp = self.session.get(pdf_url, headers=self.headers, verify=False, timeout=60)
            with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
                text = ""
                for i in range(min(5, len(pdf.pages))):
                    t = pdf.pages[i].extract_text()
                    if t: text += t + "\n"
                return text
        except Exception as e:
            print(f"❌ Ошибка чтения PDF: {e}")
            return None

    def analyze_with_gpt(self, text, title):
        if not OPENAI_API_KEY: return "⚠️ Нет ключа AI."
        print("🧠 GPT Анализ...")
        try:
            from openai import OpenAI
            client = OpenAI(api_key=OPENAI_API_KEY)
            prompt = f"Проанализируй отчет ЦБ РФ '{title}'. Дай сигнал для ОФЗ. Текст: {text[:8000]}"
            response = client.chat.completions.create(
                model="gpt-4o", messages=[{"role": "user", "content": prompt}], temperature=0.3
            )
            return response.choices[0].message.content
        except Exception as e:
            return f"GPT Error: {e}"

    def run(self):
        print("🔍 ЗАПУСК ОТЛАДКИ ЦБ...")
        url = "https://www.cbr.ru/calendar"
        
        soup = self.get_soup(url)
        if not soup: return

        links = soup.find_all('a')
        print(f"Всего ссылок в календаре: {len(links)}")

        for link in links:
            title = link.get_text(strip=True)
            href = link.get('href')
            
            if not href or not title: continue
            
            # Ищем совпадение по названию
            is_target = any(t.lower() in title.lower() for t in self.targets)
            
            if is_target:
                print(f"\n🎯 ЦЕЛЬ НАЙДЕНА В КАЛЕНДАРЕ: {title}")
                print(f"   Ссылка: {href}")
                
                full_url = urljoin("https://www.cbr.ru", href)
                
                # Заходим внутрь
                print(f"   ➡️ Заходим внутрь: {full_url}")
                sub_soup = self.get_soup(full_url)
                
                if sub_soup:
                    # Выводим ВСЕ ссылки на PDF, которые там есть
                    all_pdfs = sub_soup.find_all('a', href=re.compile(r'\.pdf$', re.IGNORECASE))
                    print(f"   📄 Найдено PDF внутри: {len(all_pdfs)}")
                    
                    for pdf in all_pdfs:
                        pdf_href = pdf['href']
                        print(f"      - {pdf_href}")
                        
                        # Пытаемся найти 2025 год
                        if "2025" in pdf_href:
                            print("      ✅ ЭТО 2025! ОБРАБАТЫВАЕМ...")
                            target_pdf = urljoin("https://www.cbr.ru", pdf_href)
                            text = self.extract_text_from_pdf(target_pdf)
                            if text:
                                ans = self.analyze_with_gpt(text, title)
                                self.send_telegram(f"🐞 **DEBUG MODE**\n\n📄 {title}\n\n{ans}\n🔗 {target_pdf}")
                                return # После первого успеха останавливаемся (для теста)
                        else:
                            print("      ❌ Не 2025 год, пропускаем.")
                else:
                    print("   ❌ Не удалось открыть страницу новости.")

        print("\n✅ Отладка завершена.")

if __name__ == "__main__":
    CBRAgent().run()
