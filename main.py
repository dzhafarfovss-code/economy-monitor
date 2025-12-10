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

class MacroAgent:
    def __init__(self):
        self.history_file = "history.json"
        self.processed_urls = self.load_history()
        
        self.session = requests.Session()
        retries = Retry(total=3, backoff_factor=2, status_forcelist=[500, 502, 503, 504])
        self.session.mount('https://', HTTPAdapter(max_retries=retries))

        # 1. СПИСОК ЦЕЛЕЙ (Отчеты)
        self.targets_cbr = [
            r"Обзор рисков",
            r"Региональная экономика",
            r"Макроэкономический опрос",
            r"Денежно-кредитные условия",
            r"Мониторинг отраслевых",
            r"Доклад о денежно-кредитной"
        ]
        
        self.targets_minec = [
            r"О текущей ситуации",
            r"Картина деловой активности",
            r"Экономический обзор"
        ]

        # 2. ФИЛЬТР ДАТЫ (Самое важное!)
        # Ищем только конец года (Ноябрь, Декабрь 2025)
        # Это захватит "вчера", "сегодня" и "неделю назад", но отсечет старье.
        self.valid_dates = [
            "декабря 2025", "ноября 2025",  # Текст на сайте (рус)
            "12.2025", "11.2025",           # Даты в ссылках
            "2025-12", "2025-11",           # Формат ISO
            "_12_25", "_11_25"              # В названиях файлов
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
        print(f"📤 TG Out: {message[:30]}...")
        for chunk in [message[i:i+4000] for i in range(0, len(message), 4000)]:
            url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
            try:
                self.session.post(url, data={"chat_id": TG_CHAT_ID, "text": chunk, "parse_mode": "Markdown"}, timeout=15)
                time.sleep(1)
            except Exception as e:
                print(f"TG Error: {e}")

    def get_soup(self, url, source="generic"):
        # Маскировка под Яндекс для всех
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; YandexBot/3.0; +http://yandex.com/bots)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        }
        try:
            resp = self.session.get(url, headers=headers, verify=False, timeout=60)
            resp.encoding = resp.apparent_encoding
            return BeautifulSoup(resp.text, 'html.parser')
        except Exception as e:
            print(f"⚠️ Сбой ({url}): {e}")
            return None

    def extract_text_from_pdf(self, pdf_url):
        print(f"⬇️ PDF: {pdf_url}")
        try:
            headers = {"User-Agent": "Mozilla/5.0 (compatible; YandexBot/3.0)"}
            resp = self.session.get(pdf_url, headers=headers, verify=False, timeout=60)
            with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
                text = ""
                # Читаем 7 страниц
                for i in range(min(7, len(pdf.pages))):
                    t = pdf.pages[i].extract_text()
                    if t: text += t + "\n"
                return text
        except Exception as e:
            print(f"PDF Fail: {e}")
            return None

    def analyze_with_gpt(self, text, title, source_name):
        if not OPENAI_API_KEY: return "⚠️ Нет AI ключа."
        print("🧠 GPT Анализ...")
        try:
            from openai import OpenAI
            client = OpenAI(api_key=OPENAI_API_KEY)
            prompt = f"""
            Ты — макроэкономист. Проанализируй: "{title}" ({source_name}).
            Дай сигнал трейдеру ОФЗ.
            СТРУКТУРА:
            1. 🦅 **Риторика:** (Жесткая/Мягкая).
            2. 📊 **Факты:** (Инфляция, Ожидания, Кредиты).
            3. 🏛 **Вывод для ОФЗ:** (Покупать/Продавать).
            4. 🔥 **Риск:** (Если есть).
            Текст: {text[:12000]}
            """
            response = client.chat.completions.create(
                model="gpt-4o", messages=[{"role": "user", "content": prompt}], temperature=0.3
            )
            return response.choices[0].message.content
        except Exception as e:
            return f"AI Error: {e}"

    def is_fresh(self, text_to_check):
        """Проверка: содержит ли текст упоминание Ноября или Декабря 2025"""
        if not text_to_check: return False
        # Проверяем "2025"
        if "2025" not in text_to_check: return False
        
        # Проверяем наличие маркеров месяца (ноябрь/декабрь)
        # Это отсечет январь-октябрь 2025
        for date_marker in self.valid_dates:
            if date_marker in text_to_check:
                return True
        return False

    def check_cbr(self):
        print("🔍 [ЦБ] Проверка...")
        urls = ["https://www.cbr.ru/calendar"] # Календарь - самое надежное
        
        for start_url in urls:
            soup = self.get_soup(start_url)
            if not soup: continue

            for link in soup.find_all('a'):
                title = link.get_text(strip=True)
                href = link.get('href')
                if not href or not title: continue
                
                # === ГЛАВНЫЙ ФИЛЬТР ===
                # Проверяем, есть ли в заголовке или ссылке нужная дата (Ноя/Дек 2025)
                full_check_string = (title + href).lower()
                
                # Если в заголовке нет 2025 - сразу мимо
                if "2025" not in title: continue

                # Если нет маркеров конца года (чтобы не брать старье)
                is_fresh_date = any(d in title.lower() for d in ["ноября", "декабря"])
                # Если в заголовке нет месяца, но есть 2025 - можно рискнуть проверить
                
                is_target = any(re.search(p, title, re.IGNORECASE) for p in self.targets_cbr)
                
                if is_target:
                    full_url = urljoin("https://www.cbr.ru", href)
                    if full_url in self.processed_urls: 
                        print(f"Пропуск (уже было): {title}")
                        continue
                    
                    print(f"🔥 НАЙДЕН КАНДИДАТ: {title}")
                    
                    # Пытаемся найти PDF
                    pdf_url = full_url if href.lower().endswith('.pdf') else None
                    if not pdf_url:
                        sub = self.get_soup(full_url)
                        if sub:
                            pl = sub.find('a', href=re.compile(r'\.pdf$', re.IGNORECASE))
                            if pl: pdf_url = urljoin("https://www.cbr.ru", pl['href'])
                    
                    if pdf_url:
                        text = self.extract_text_from_pdf(pdf_url)
                        if text:
                            # Доп. проверка: а вдруг PDF старый? (2022 год)
                            # Смотрим первые 500 символов PDF на наличие 2025
                            if "2025" in text[:500] or "2025" in title:
                                ans = self.analyze_with_gpt(text, title, "ЦБ РФ")
                                self.send_telegram(f"🏦 **ЦБ РФ**\n\n📄 {title}\n\n{ans}\n🔗 {pdf_url}")
                                self.save_history(full_url)
                            else:
                                print("PDF оказался старым (не 2025).")

    def check_minec(self):
        print("🔍 [МИНЭК] Проверка...")
        url = "https://www.economy.gov.ru/material/directions/makroec/ekonomicheskie_obzory/"
        soup = self.get_soup(url) 
        if not soup: return

        for link in soup.find_all('a'):
            title = link.get_text(strip=True)
            href = link.get('href')
            if not href or not title: continue
            
            # Фильтр на Ноябрь/Декабрь 2025
            if "2025" not in title: continue
            
            is_target = any(re.search(p, title, re.IGNORECASE) for p in self.targets_minec)
            if is_target:
                full_url = urljoin("https://www.economy.gov.ru", href)
                if full_url in self.processed_urls: continue
                
                print(f"🔥 НАЙДЕН МИНЭК: {title}")
                sub = self.get_soup(full_url)
                if sub:
                    pl = sub.find('a', href=re.compile(r'\.pdf$', re.IGNORECASE))
                    if pl:
                        p_url = urljoin("https://www.economy.gov.ru", pl['href'])
                        text = self.extract_text_from_pdf(p_url)
                        if text:
                            ans = self.analyze_with_gpt(text, title, "МинЭк")
                            self.send_telegram(f"📉 **МИНЭК**\n\n📄 {title}\n\n{ans}\n🔗 {p_url}")
                            self.save_history(full_url)

    def run(self):
        self.check_cbr()
        self.check_minec()
        print("✅ Готово")

if __name__ == "__main__":
    MacroAgent().run()
