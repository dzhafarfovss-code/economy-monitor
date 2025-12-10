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

# --- НАСТРОЙКИ (Берем из GitHub Secrets) ---
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

# Отключаем предупреждения SSL (для Минэка часто нужно)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

class MacroAgent:
    def __init__(self):
        self.history_file = "history.json"
        self.processed_urls = self.load_history()
        
        # Заголовки, чтобы сайты думали, что мы браузер
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        }

        # Ключевые слова для поиска (Регулярные выражения)
        self.targets_cbr = [
            r"Обзор рисков финансовых рынков",
            r"Региональная экономика",
            r"Макроэкономический опрос",
            r"Денежно-кредитные условия",
            r"Мониторинг отраслевых финансовых потоков"
        ]
        
        self.targets_minec = [
            r"О текущей ситуации",
            r"Картина деловой активности",
            r"Экономический обзор"
        ]

    def load_history(self):
        """Загружает список уже прочитанных ссылок"""
        if os.path.exists(self.history_file):
            try:
                with open(self.history_file, 'r') as f:
                    return set(json.load(f))
            except:
                return set()
        return set()

    def save_history(self, url):
        """Сохраняет ссылку, чтобы не спамить повторно"""
        self.processed_urls.add(url)
        with open(self.history_file, 'w') as f:
            json.dump(list(self.processed_urls), f)

    def send_telegram(self, message):
        """Отправка в Telegram с разбивкой длинных сообщений"""
        if not TG_BOT_TOKEN or not TG_CHAT_ID:
            print("!!! ОШИБКА: Нет ключей Telegram в Secrets")
            return

        # Телеграм не принимает сообщения длиннее 4096 символов
        for chunk in [message[i:i+4000] for i in range(0, len(message), 4000)]:
            url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
            data = {
                "chat_id": TG_CHAT_ID, 
                "text": chunk, 
                "parse_mode": "Markdown"
            }
            try:
                requests.post(url, data=data, timeout=10)
            except Exception as e:
                print(f"Ошибка отправки TG: {e}")

    def get_soup(self, url):
        """Скачивает страницу и делает Суп"""
        try:
            resp = requests.get(url, headers=self.headers, verify=False, timeout=20)
            resp.raise_for_status()
            resp.encoding = resp.apparent_encoding
            return BeautifulSoup(resp.text, 'html.parser')
        except Exception as e:
            print(f"Ошибка доступа к {url}: {e}")
            return None

    def extract_text_from_pdf(self, pdf_url):
        """Качает PDF и вытаскивает текст первых страниц"""
        print(f"⬇️ Скачиваем PDF: {pdf_url}")
        try:
            resp = requests.get(pdf_url, headers=self.headers, verify=False, timeout=30)
            with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
                text = ""
                # Читаем только первые 7 страниц (там суть, дальше таблицы)
                max_pages = min(7, len(pdf.pages))
                for i in range(max_pages):
                    page_text = pdf.pages[i].extract_text()
                    if page_text:
                        text += page_text + "\n"
                return text
        except Exception as e:
            print(f"Ошибка чтения PDF: {e}")
            return None

    def analyze_with_gpt(self, text, title, source_name):
        """Анализ текста через OpenAI"""
        if not OPENAI_API_KEY:
            return "⚠️ Ключ OpenAI не найден. Вот начало текста:\n" + text[:600] + "..."

        print("🧠 Отправляем данные в GPT...")
        try:
            from openai import OpenAI
            client = OpenAI(api_key=OPENAI_API_KEY)

            prompt = f"""
            Ты — опытный макроэкономист и трейдер облигациями (ОФЗ).
            Проанализируй документ: "{title}" от {source_name}.
            
            Твоя задача — дать четкие сигналы для рынка. Не лей воду.
            
            СТРУКТУРА ОТВЕТА:
            1. 🦅 **Риторика:** (Жесткая / Умеренно-жесткая / Нейтральная / Мягкая). Почему? (1-2 предложения).
            2. 📈 **Инфляция и Ставка:** Главные цифры и прогноз. Есть ли признаки замедления инфляции или перегрева?
            3. 🏛 **ОФЗ и Рынок:** Что делать с гособлигациями? (Покупать короткие/длинные, продавать, держать). Риски?
            4. 🔥 **Важное:** Если есть что-то экстраординарное (рекордный дефицит кадров, обвал экспорта и т.д.).

            Текст документа (начало):
            {text[:12000]}
            """

            response = client.chat.completions.create(
                model="gpt-4o", 
                messages=[{"role": "user", "content": prompt}],
                temperature=0.4
            )
            return response.choices[0].message.content
        except Exception as e:
            return f"Ошибка GPT: {e}. Сырой текст: {text[:500]}..."

    # --- ЛОГИКА ЦБ РФ ---
    def check_cbr(self):
        print("🔍 [ЦБ РФ] Проверка календаря...")
        base_url = "https://www.cbr.ru"
        calendar_url = "https://www.cbr.ru/calendar"
        
        soup = self.get_soup(calendar_url)
        if not soup: return

        links = soup.find_all('a')
        
        for link in links:
            title = link.get_text(strip=True)
            href = link.get('href')
            
            if not href or not title: continue
            
            # Проверка по ключевым словам
            is_target = any(re.search(pattern, title, re.IGNORECASE) for pattern in self.targets_cbr)
            
            if is_target:
                full_url = urljoin(base_url, href)
                if full_url in self.processed_urls: continue
                
                print(f"🔥 НАЙДЕН ОТЧЕТ ЦБ: {title}")
                
                # Ищем PDF
                pdf_url = None
                
                # Если ссылка сразу на PDF
                if href.lower().endswith('.pdf'):
                    pdf_url = full_url
                else:
                    # Заходим внутрь новости
                    sub_soup = self.get_soup(full_url)
                    if sub_soup:
                        pdf_link = sub_soup.find('a', href=re.compile(r'\.pdf$', re.IGNORECASE))
                        if pdf_link:
                            pdf_url = urljoin(base_url, pdf_link['href'])

                if pdf_url:
                    text = self.extract_text_from_pdf(pdf_url)
                    if text:
                        analysis = self.analyze_with_gpt(text, title, "Банка России")
                        msg = f"🏦 **ЦБ РФ: ВЫШЕЛ ОТЧЕТ**\n\n📄 *{title}*\n\n{analysis}\n\n🔗 [Документ]({pdf_url})"
                        self.send_telegram(msg)
                        self.save_history(full_url)

    # --- ЛОГИКА МИНЭК (Росстат данные) ---
    def check_minec(self):
        print("🔍 [МИНЭК] Проверка обзоров...")
        base_url = "https://www.economy.gov.ru"
        section_url = "https://www.economy.gov.ru/material/directions/makroec/ekonomicheskie_obzory/"
        
        soup = self.get_soup(section_url)
        if not soup: return

        # Минэк часто меняет верстку, ищем просто ссылки с нужным текстом
        links = soup.find_all('a')
        
        for link in links:
            title = link.get_text(strip=True)
            href = link.get('href')
            
            if not href or not title: continue
            
            is_target = any(re.search(pattern, title, re.IGNORECASE) for pattern in self.targets_minec)
            
            if is_target:
                full_url = urljoin(base_url, href)
                if full_url in self.processed_urls: continue
                
                print(f"🔥 НАЙДЕН ОБЗОР МИНЭКА: {title}")
                
                # Заходим внутрь
                sub_soup = self.get_soup(full_url)
                if sub_soup:
                    pdf_link = sub_soup.find('a', href=re.compile(r'\.pdf$', re.IGNORECASE))
                    if pdf_link:
                        pdf_url = urljoin(base_url, pdf_link['href'])
                        text = self.extract_text_from_pdf(pdf_url)
                        
                        if text:
                            analysis = self.analyze_with_gpt(text, title, "Минэкономразвития")
                            msg = f"📉 **МИНЭК (ДАННЫЕ РОССТАТА)**\n\n📄 *{title}*\n\n{analysis}\n\n🔗 [Документ]({pdf_url})"
                            self.send_telegram(msg)
                            self.save_history(full_url)

    def run(self):
        self.check_cbr()
        self.check_minec()
        print("✅ Цикл проверки завершен.")

if __name__ == "__main__":
    agent = MacroAgent()
    agent.run()
