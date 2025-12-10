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
# Если ключей нет, скрипт не упадет, а просто напишет ошибку в лог
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

# Отключаем предупреждения SSL (сайты госорганов часто имеют кривые сертификаты)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

class MacroAgent:
    def __init__(self):
        self.history_file = "history.json"
        self.processed_urls = self.load_history()
        
        # Притворяемся обычным браузером
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        }

        # ЧТО ИЩЕМ У ЦБ (Ключевые слова)
        self.targets_cbr = [
            r"Обзор рисков финансовых рынков",
            r"Региональная экономика",
            r"Макроэкономический опрос",
            r"Денежно-кредитные условия",
            r"Мониторинг отраслевых финансовых потоков",
            r"Доклад о денежно-кредитной политике"
        ]
        
        # ЧТО ИЩЕМ У МИНЭКА
        self.targets_minec = [
            r"О текущей ситуации",
            r"Картина деловой активности",
            r"Экономический обзор"
        ]

    def load_history(self):
        """Загружает список уже обработанных ссылок"""
        if os.path.exists(self.history_file):
            try:
                with open(self.history_file, 'r') as f:
                    return set(json.load(f))
            except:
                return set()
        return set()

    def save_history(self, url):
        """Запоминает ссылку, чтобы не слать повторы"""
        self.processed_urls.add(url)
        with open(self.history_file, 'w') as f:
            json.dump(list(self.processed_urls), f)

    def send_telegram(self, message):
        """Отправка в Telegram"""
        if not TG_BOT_TOKEN or not TG_CHAT_ID:
            print("!!! TG Ключи не найдены в Secrets")
            return

        # Телеграм не принимает сообщения длиннее 4096 символов, режем на куски
        for chunk in [message[i:i+4000] for i in range(0, len(message), 4000)]:
            url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
            data = {
                "chat_id": TG_CHAT_ID, 
                "text": chunk, 
                "parse_mode": "Markdown"
            }
            try:
                requests.post(url, data=data, timeout=10)
                time.sleep(1) # Пауза между сообщениями
            except Exception as e:
                print(f"Ошибка отправки TG: {e}")

    def get_soup(self, url):
        """Скачивает страницу"""
        try:
            resp = requests.get(url, headers=self.headers, verify=False, timeout=20)
            resp.raise_for_status()
            # Фикс кодировки
            resp.encoding = resp.apparent_encoding
            return BeautifulSoup(resp.text, 'html.parser')
        except Exception as e:
            print(f"Ошибка доступа к {url}: {e}")
            return None

    def extract_text_from_pdf(self, pdf_url):
        """Качает PDF и читает текст (первые 7 страниц)"""
        print(f"⬇️ Скачиваем PDF: {pdf_url}")
        try:
            resp = requests.get(pdf_url, headers=self.headers, verify=False, timeout=30)
            with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
                text = ""
                # Читаем только первые 7 страниц (там вся суть для трейдинга)
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
        """Анализ через GPT-4o"""
        if not OPENAI_API_KEY:
            return "⚠️ Ключ OpenAI не найден. Вот начало текста:\n" + text[:600] + "..."

        print("🧠 Думаем (запрос к AI)...")
        try:
            from openai import OpenAI
            client = OpenAI(api_key=OPENAI_API_KEY)

            prompt = f"""
            Ты — опытный макроэкономист и трейдер облигациями (ОФЗ).
            Проанализируй документ: "{title}" от {source_name}.
            
            Дай СУХУЮ выжимку для принятия инвестиционных решений.
            
            СТРУКТУРА ОТВЕТА:
            1. 🦅 **Риторика:** (Жесткая/Мягкая/Нейтральная). Почему?
            2. 📊 **Главные цифры:** (Инфляция, ожидания, кадровый голод, потоки в ОФЗ).
            3. 🏛 **Влияние на ОФЗ:** (Покупать/Продавать/Держать). Есть ли смена тренда?
            4. 🔥 **Риски:** Что может пойти не так?

            Текст документа (первые страницы):
            {text[:12000]}
            """

            response = client.chat.completions.create(
                model="gpt-4o", 
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3
            )
            return response.choices[0].message.content
        except Exception as e:
            return f"Ошибка GPT: {e}. Сырой текст: {text[:500]}..."

    # --- ПРОВЕРКА ЦБ РФ (Календарь событий) ---
    def check_cbr(self):
        print("🔍 [ЦБ РФ] Проверка календаря...")
        base_url = "https://www.cbr.ru"
        calendar_url = "https://www.cbr.ru/calendar"
        
        soup = self.get_soup(calendar_url)
        if not soup: return

        # Ищем все ссылки на странице
        links = soup.find_all('a')
        
        for link in links:
            title = link.get_text(strip=True)
            href = link.get('href')
            
            if not href or not title: continue
            
            # Проверяем название по нашему списку
            is_target = any(re.search(pattern, title, re.IGNORECASE) for pattern in self.targets_cbr)
            
            if is_target:
                full_url = urljoin(base_url, href)
                
                # Если уже видели - пропускаем
                if full_url in self.processed_urls: continue
                
                print(f"🔥 НАЙДЕН ОТЧЕТ ЦБ: {title}")
                
                # Ищем PDF внутри страницы
                pdf_url = None
                
                # Иногда ссылка ведет сразу на файл
                if href.lower().endswith('.pdf'):
                    pdf_url = full_url
                else:
                    # Заходим внутрь новости
                    sub_soup = self.get_soup(full_url)
                    if sub_soup:
                        # Ищем ссылку на PDF
                        pdf_link = sub_soup.find('a', href=re.compile(r'\.pdf$', re.IGNORECASE))
                        # Иногда кнопка называется "Скачать"
                        if not pdf_link:
                            pdf_link = sub_soup.find('a', string=re.compile(r'Скачать|Полный текст', re.IGNORECASE))
                            
                        if pdf_link:
                            pdf_url = urljoin(base_url, pdf_link['href'])

                if pdf_url:
                    text = self.extract_text_from_pdf(pdf_url)
                    if text:
                        analysis = self.analyze_with_gpt(text, title, "Банка России")
                        msg = f"🏦 **ЦБ РФ: НОВЫЙ ОТЧЕТ**\n\n📄 *{title}*\n\n{analysis}\n\n🔗 [Читать оригинал]({pdf_url})"
                        self.send_telegram(msg)
                        self.save_history(full_url)
                    else:
                        print("Не удалось прочитать текст PDF")
                else:
                    print(f"PDF не найден на странице {full_url}")

    # --- ПРОВЕРКА МИНЭК (Росстат данные) ---
    def check_minec(self):
        print("🔍 [МИНЭК] Проверка обзоров...")
        base_url = "https://www.economy.gov.ru"
        # Раздел обзоров
        section_url = "https://www.economy.gov.ru/material/directions/makroec/ekonomicheskie_obzory/"
        
        soup = self.get_soup(section_url)
        if not soup: return

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
                    # Ищем PDF
                    pdf_link = sub_soup.find('a', href=re.compile(r'\.pdf$', re.IGNORECASE))
                    if pdf_link:
                        pdf_url = urljoin(base_url, pdf_link['href'])
                        text = self.extract_text_from_pdf(pdf_url)
                        
                        if text:
                            analysis = self.analyze_with_gpt(text, title, "Минэкономразвития")
                            msg = f"📉 **МИНЭК (ДАННЫЕ РОССТАТА)**\n\n📄 *{title}*\n\n{analysis}\n\n🔗 [Читать оригинал]({pdf_url})"
                            self.send_telegram(msg)
                            self.save_history(full_url)

    def run(self):
        # Запускаем проверки последовательно
        self.check_cbr()
        self.check_minec()
        print("✅ Проверка завершена. Скрипт засыпает.")

if __name__ == "__main__":
    agent = MacroAgent()
    agent.run()
