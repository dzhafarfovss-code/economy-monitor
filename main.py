import requests
from bs4 import BeautifulSoup
import pdfplumber
import io
import re
import urllib3
from urllib.parse import urljoin
import time
import os
import json
from collections import Counter

# --- НАСТРОЙКИ TELEGRAM ---
TG_BOT_TOKEN = "8592284171:AAELv1GTxEX8aybp_iVZYwsMNKvXm8eQVgE"  # Вставьте сюда токен
TG_CHAT_ID = "@shml_d"        # Вставьте сюда ваш ID (числом или строкой)

# Отключаем предупреждения SSL
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

class EconomyMonitor:
    def __init__(self, check_interval=300):
        self.base_url = "https://www.economy.gov.ru"
        self.section_url = "https://www.economy.gov.ru/material/directions/makroec/ekonomicheskie_obzory/"
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Connection": "keep-alive"
        }
        self.check_interval = check_interval
        self.history_file = "history.json"
        self.last_processed_url = self.load_history()

    def send_telegram(self, message):
        """Отправка сообщения в Telegram"""
        try:
            url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
            data = {
                "chat_id": TG_CHAT_ID,
                "text": message,
                "parse_mode": "Markdown" # Можно использовать жирный шрифт и т.д.
            }
            requests.post(url, data=data, timeout=10)
            print("[+] Уведомление отправлено в Telegram")
        except Exception as e:
            print(f"[!] Ошибка отправки в Telegram: {e}")

    def load_history(self):
        if os.path.exists(self.history_file):
            with open(self.history_file, 'r') as f:
                try:
                    return json.load(f).get("last_url", "")
                except:
                    return ""
        return ""

    def save_history(self, url):
        with open(self.history_file, 'w') as f:
            json.dump({"last_url": url}, f)
        self.last_processed_url = url

    def get_soup(self, url):
        try:
            response = requests.get(url, headers=self.headers, verify=False, timeout=15)
            response.raise_for_status()
            response.encoding = response.apparent_encoding 
            return BeautifulSoup(response.text, 'html.parser')
        except Exception as e:
            print(f"Ошибка доступа к сайту: {e}")
            return None

    def find_latest_review(self):
        soup = self.get_soup(self.section_url)
        if not soup: return None, None
        
        # Ищем ключевые слова в ссылках
        target_texts = ["О текущей ситуации", "Картина деловой активности", "Экономический обзор"]
        for pattern in target_texts:
            link = soup.find('a', string=re.compile(pattern, re.IGNORECASE))
            if link and 'href' in link.attrs:
                return urljoin(self.base_url, link['href']), link.text.strip()
        return None, None

    def get_pdf_text(self, article_url):
        soup = self.get_soup(article_url)
        if not soup: return None
        
        # Ищем ссылку на PDF
        pdf_link = soup.find('a', href=re.compile(r'\.pdf$', re.IGNORECASE))
        if not pdf_link: return None
        
        full_pdf_url = urljoin(self.base_url, pdf_link['href'])
        
        try:
            print(f"[*] Скачиваем PDF: {full_pdf_url}")
            resp = requests.get(full_pdf_url, headers=self.headers, verify=False, timeout=30)
            with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
                text = "\n".join([page.extract_text() or "" for page in pdf.pages])
                return text
        except Exception as e:
            print(f"Ошибка парсинга PDF: {e}")
            return None

    def make_summary(self, text):
        if not text: return "Не удалось извлечь текст из PDF (возможно, это картинка)."
        
        # Простая эвристика: ищем предложения с цифрами и ключевыми словами
        text = text.replace('\n', ' ')
        sentences = re.split(r'(?<=\.)\s', text)
        keywords = ["инфляц", "цен", "ввп", "%", "рост", "снижен"]
        
        important = []
        for s in sentences:
            if len(s) > 20 and any(k in s.lower() for k in keywords):
                # Берем только если есть цифры
                if re.search(r'\d', s):
                    important.append(s.strip())
        
        # Возвращаем топ-7 предложений
        return "\n\n🔹 ".join(important[:7])

    def start(self):
        print(f"🚀 Мониторинг запущен. Проверка каждые {self.check_interval} сек.")
        self.send_telegram("🤖 Бот запущен и следит за МинЭкономРазвития.")
        
        while True:
            try:
                url, title = self.find_latest_review()
                
                if url:
                    if url != self.last_processed_url:
                        print(f"\n[!] НОВЫЙ ОТЧЕТ: {title}")
                        
                        text = self.get_pdf_text(url)
                        summary = self.make_summary(text)
                        
                        # Формируем сообщение
                        msg = (f"🔥 *ВЫШЕЛ НОВЫЙ ОТЧЕТ МИНЭКА*\n"
                               f"📄 {title}\n\n"
                               f"📊 *Главное:*\n🔹 {summary}\n\n"
                               f"🔗 [Ссылка на статью]({url})")
                        
                        # Шлем в телегу
                        self.send_telegram(msg)
                        
                        self.save_history(url)
                    else:
                        print(f"[{time.strftime('%H:%M')}] Новых отчетов нет.")
                
            except Exception as e:
                print(f"Ошибка в цикле: {e}")
            
            time.sleep(self.check_interval)

if __name__ == "__main__":
    # Ставим 300 секунд (5 минут) — идеальный баланс
    bot = EconomyMonitor(check_interval=300)
    bot.start()
