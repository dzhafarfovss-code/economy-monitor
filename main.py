import os
import requests
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
        
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }

        self.targets_cbr = [
            r"Обзор рисков",
            r"Региональная экономика",
            r"Макроэкономический опрос",
            r"Денежно-кредитные условия",
            r"Мониторинг отраслевых"
        ]

    def load_history(self):
        # В РЕЖИМЕ ТЕСТА ИГНОРИРУЕМ ИСТОРИЮ (чтобы он прислал то, что уже видел)
        return set()

    def save_history(self, url):
        pass # В тесте не сохраняем

    def send_telegram(self, message):
        if not TG_BOT_TOKEN or not TG_CHAT_ID:
            print("!!! НЕТ КЛЮЧЕЙ ТЕЛЕГРАМА")
            return

        print(f"📤 Отправка в Telegram: {message[:50]}...")
        url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
        data = {"chat_id": TG_CHAT_ID, "text": message, "parse_mode": "Markdown"}
        try:
            requests.post(url, data=data, timeout=10)
        except Exception as e:
            print(f"Ошибка TG: {e}")

    def get_soup(self, url):
        try:
            resp = requests.get(url, headers=self.headers, verify=False, timeout=30)
            return BeautifulSoup(resp.text, 'html.parser')
        except:
            return None

    def extract_text_from_pdf(self, pdf_url):
        print(f"⬇️ Качаем: {pdf_url}")
        try:
            resp = requests.get(pdf_url, headers=self.headers, verify=False, timeout=60)
            with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
                text = ""
                for i in range(min(5, len(pdf.pages))):
                    t = pdf.pages[i].extract_text()
                    if t: text += t + "\n"
                return text
        except Exception as e:
            print(f"PDF Fail: {e}")
            return None

    def analyze_with_gpt(self, text, title):
        if not OPENAI_API_KEY:
            return "⚠️ Нет ключа OpenAI."

        print("🧠 GPT Анализирует...")
        try:
            from openai import OpenAI
            client = OpenAI(api_key=OPENAI_API_KEY)

            prompt = f"""
            Проанализируй документ ЦБ РФ: "{title}".
            Дай краткую суть для трейдера ОФЗ (3 пункта).
            Текст: {text[:8000]}
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
        # 1. ПРОВЕРКА СВЯЗИ
        self.send_telegram("👋 **Бот запущен!** Начинаю проверку ЦБ...")

        # 2. ПРОВЕРКА ЦБ
        print("🔍 Идем на сайт ЦБ...")
        base_url = "https://www.cbr.ru"
        # Смотрим раздел аналитики, там ссылки стабильнее
        start_url = "https://www.cbr.ru/analytics/fin_stab/" 
        
        soup = self.get_soup(start_url)
        if not soup:
            print("Сайт ЦБ не открылся")
            return

        links = soup.find_all('a')
        count = 0
        
        for link in links:
            title = link.get_text(strip=True)
            href = link.get('href')
            
            if not href or not title: continue
            
            # Ищем совпадения по названиям
            is_target = any(re.search(p, title, re.IGNORECASE) for p in self.targets_cbr)
            
            if is_target:
                # В ТЕСТЕ БЕРЕМ ТОЛЬКО ПЕРВЫЙ НАЙДЕННЫЙ ДОКУМЕНТ И СТОП
                if count >= 1: break 
                
                full_url = urljoin(base_url, href)
                print(f"🔥 Тестовая находка: {title}")
                
                pdf_url = full_url if href.endswith('.pdf') else None
                if not pdf_url:
                    # Пробуем найти PDF внутри
                    sub = self.get_soup(full_url)
                    if sub:
                        pl = sub.find('a', href=re.compile(r'\.pdf$', re.IGNORECASE))
                        if pl: pdf_url = urljoin(base_url, pl['href'])
                
                if pdf_url:
                    text = self.extract_text_from_pdf(pdf_url)
                    if text:
                        ans = self.analyze_with_gpt(text, title)
                        self.send_telegram(f"🧪 **ТЕСТОВЫЙ ПРОГОН**\n\n📄 {title}\n\n{ans}\n🔗 {pdf_url}")
                        count += 1

        if count == 0:
            self.send_telegram("Ничего не нашел даже для теста. Странно.")

if __name__ == "__main__":
    MacroAgent().run()
