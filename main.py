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
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        }

        self.targets_cbr = [
            r"Обзор рисков финансовых рынков",
            r"Региональная экономика",
            r"Макроэкономический опрос",
            r"Денежно-кредитные условия",
            r"Мониторинг отраслевых финансовых потоков",
            r"Доклад о денежно-кредитной политике"
        ]
        
        self.targets_minec = [
            r"О текущей ситуации",
            r"Картина деловой активности",
            r"Экономический обзор"
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
        if not TG_BOT_TOKEN or not TG_CHAT_ID:
            print("!!! TG Keys missing")
            return

        for chunk in [message[i:i+4000] for i in range(0, len(message), 4000)]:
            url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
            data = {"chat_id": TG_CHAT_ID, "text": chunk, "parse_mode": "Markdown"}
            try:
                requests.post(url, data=data, timeout=10)
                time.sleep(1)
            except Exception as e:
                print(f"TG Error: {e}")

    def get_soup(self, url, timeout=20):
        try:
            resp = requests.get(url, headers=self.headers, verify=False, timeout=timeout)
            resp.raise_for_status()
            resp.encoding = resp.apparent_encoding
            return BeautifulSoup(resp.text, 'html.parser')
        except Exception as e:
            print(f"Connection Error ({url}): {e}")
            return None

    def extract_text_from_pdf(self, pdf_url):
        print(f"⬇️ PDF: {pdf_url}")
        try:
            resp = requests.get(pdf_url, headers=self.headers, verify=False, timeout=45)
            with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
                text = ""
                # Берем первые 5 страниц (достаточно для анализа)
                for i in range(min(5, len(pdf.pages))):
                    t = pdf.pages[i].extract_text()
                    if t: text += t + "\n"
                return text
        except Exception as e:
            print(f"PDF Error: {e}")
            return None

    def analyze_with_gpt(self, text, title, source_name):
        if not OPENAI_API_KEY:
            return "⚠️ AI Key missing. Text start:\n" + text[:500]

        print("🧠 AI Analysis...")
        try:
            from openai import OpenAI
            client = OpenAI(api_key=OPENAI_API_KEY)

            prompt = f"""
            Ты — макроэкономист. Проанализируй: "{title}" ({source_name}).
            Дай краткую выжимку для инвестора в ОФЗ и Рубль.
            
            СТРУКТУРА:
            1. 🦅 **Риторика:** (Жесткая/Мягкая).
            2. 📊 **Главные цифры:** (Инфляция, ожидания, кредитование).
            3. 🏛 **Вывод для ОФЗ:** (Покупать/Продавать).
            4. 🔥 **Риски:** (Если есть).

            Текст:
            {text[:10000]}
            """

            response = client.chat.completions.create(
                model="gpt-4o", 
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3
            )
            return response.choices[0].message.content
        except Exception as e:
            return f"AI Error: {e}"

    def check_cbr(self):
        print("🔍 [ЦБ] Сканирование...")
        # Смотрим не только календарь, но и раздел аналитики, там надежнее
        urls_to_check = [
            "https://www.cbr.ru/calendar",
            "https://www.cbr.ru/analytics/dkp/hosting/", # Обзоры ДКП
            "https://www.cbr.ru/analytics/fin_stab/"     # Обзоры рисков
        ]
        
        for base_section in urls_to_check:
            soup = self.get_soup(base_section)
            if not soup: continue

            links = soup.find_all('a')
            for link in links:
                title = link.get_text(strip=True)
                href = link.get('href')
                
                if not href or not title: continue
                
                # === ФИЛЬТР 2025 ГОДА ===
                # Если в названии или ссылке нет "2025" - пропускаем
                if "2025" not in title and "2025" not in href:
                    continue

                is_target = any(re.search(p, title, re.IGNORECASE) for p in self.targets_cbr)
                
                if is_target:
                    full_url = urljoin("https://www.cbr.ru", href)
                    if full_url in self.processed_urls: continue
                    
                    print(f"🔥 НАЙДЕН СВЕЖИЙ ОТЧЕТ: {title}")
                    
                    pdf_url = None
                    if href.lower().endswith('.pdf'):
                        pdf_url = full_url
                    else:
                        sub = self.get_soup(full_url)
                        if sub:
                            pl = sub.find('a', href=re.compile(r'\.pdf$', re.IGNORECASE))
                            if pl: pdf_url = urljoin("https://www.cbr.ru", pl['href'])
                    
                    if pdf_url:
                        text = self.extract_text_from_pdf(pdf_url)
                        if text:
                            ans = self.analyze_with_gpt(text, title, "ЦБ РФ")
                            self.send_telegram(f"🏦 **ЦБ РФ (2025)**\n\n📄 {title}\n\n{ans}\n🔗 {pdf_url}")
                            self.save_history(full_url)

    def check_minec(self):
        print("🔍 [МИНЭК] Сканирование...")
        url = "https://www.economy.gov.ru/material/directions/makroec/ekonomicheskie_obzory/"
        # Увеличенный таймаут для Минэка
        soup = self.get_soup(url, timeout=45) 
        if not soup: return

        links = soup.find_all('a')
        for link in links:
            title = link.get_text(strip=True)
            href = link.get('href')
            if not href or not title: continue
            
            # Тоже фильтруем 2025
            if "2025" not in title and "2025" not in href: continue

            is_target = any(re.search(p, title, re.IGNORECASE) for p in self.targets_minec)
            if is_target:
                full_url = urljoin("https://www.economy.gov.ru", href)
                if full_url in self.processed_urls: continue
                
                print(f"🔥 НАЙДЕН МИНЭК: {title}")
                sub = self.get_soup(full_url, timeout=45)
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
