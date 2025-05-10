import os
import re
import csv
from datetime import date, timedelta
import smtplib
from email.mime.text import MIMEText
from email.header import Header
from email.utils import formataddr

import arxivscraper as ax
import requests
from tqdm import tqdm

SMOKE_TEST = True
API_KEY = os.getenv('API_KEY')
BASE_URL = os.getenv('BASE_URL')
MODEL_NAME = os.getenv('MODEL_NAME')
CATEGORIES = list(map(lambda x: x.strip(), os.getenv('CATEGORIES').split(',')))
# KEYWORDS is lower() to match the abstract.lower()
KEYWORDS = list(map(lambda x: x.strip(), os.getenv('KEYWORDS').split(',')))

class LLM:
    # setup your LLM service. The LLM should use openai api format
    def __init__(self, api_key, url, model_name):
        self.api_key = api_key
        self.url = url
        self.model_name = model_name

    def complete(self, prompt, max_tokens=1024):
        data = {
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "model": self.model_name
        }
        headers = {
            'api-key': self.api_key,
            "Authorization": f"Bearer {self.api_key}"
        }
        response = requests.post(self.url, json=data, headers=headers)
        if response.status_code == 200:
            return response.json()['choices'][0]['message']['content']
        else:
            print(f'Request failed with status code {response.status_code}.')
            return response.text

    def chat(self, messages: list, max_tokens=128):
        assert len(messages) % 2 == 1
        roles = ['user', 'assistant'] * len(messages)
        data = {
            "messages": [{"role": role, "content": message} for role, message in zip(roles, messages)],
            "max_tokens": max_tokens
        }
        headers = {
            'api-key': self.api_key,
            'Content-Type': 'application/json'
        }
        response = requests.post(self.url, json=data, headers=headers)
        if response.status_code == 200:
            return response.json()['choices'][0]['message']['content']
        else:
            print(f'Request failed with status code {response.status_code}.')
            return response.text


def fetch_papers(date_from, date_until, categories, save_meta_info=False):
    scraper = ax.Scraper(
        category='cs',
        date_from=date_from,
        date_until=date_until,
        t=30,
        filters={'categories': categories},
        timeout=1000
    )
    outputs, meta_info = scraper.scrape()

    if outputs == 1:
        return None

    if save_meta_info:
        with open('meta_info.csv', 'w', newline='') as csvfile:
            writer = csv.writer(csvfile)
            keys = list(meta_info.keys())
            writer.writerow([''] + keys)
            for i in range(len(outputs)):
                values = [meta_info[key][i] for key in keys]
                writer.writerow([i] + values)
            print('write to meta_info.csv')
    
    return outputs


def filter_papers(papers, keywords):
    filtered_papers = []
    for paper in papers:
        for key in keywords:
            if key.lower() in paper['abstract'].lower():
                highlighted_abstract = paper['abstract']
                highlighted_abstract = highlighted_abstract.replace(
                    key, f"<b>{key}</b>")
                filtered_papers.append({
                    'title': paper['title'],
                    'url': paper['url'],
                    'authors': paper['authors'],
                    'abstract': highlighted_abstract
                })
    return filtered_papers


def deduplicate_papers(papers):
    url_set = set()
    filtered_papers = []
    for paper in papers:
        if paper['url'] in url_set:
            continue
        url_set.add(paper['url'])
        filtered_papers.append(paper)
    return filtered_papers


def remove_think_content(text):
    cleaned_text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    return cleaned_text


def summarize_papers(papers):
    model = LLM(API_KEY, BASE_URL, MODEL_NAME)
    prompt = (
        '请你根据下面的论文信息，将文章的标题都翻译成中文，并且写一段简要的话概括一下文章内容。'
        '请你按照下面格式输出文章的处理结果，并且不要输出其他任何多余的文字。\n'
        '[格式]\n'
        '<b>Title:</b> 原英文标题<br>\n'
        '<b>标题:</b> 翻译后的中文标题<br>\n'
        '<b>TL;DR:</b> 你的总结<br>\n'
        '<b>摘要:</b> 翻译后的中文摘要<br>\n'
        '[论文]\n'
    )
    formated_papers = []
    for paper in papers:
        formated_papers.append(
            f"{paper['title']}\n"
            f"-URL: {paper['url']}\n"
            f"-Authors: {', '.join(paper['authors'])}\n"
            f"-Abstract: {paper['abstract']}\n\n"
        )
    summaries = []
    for i in tqdm(range(len(formated_papers))):
        formated_paper, paper = formated_papers[i], papers[i]
        summary = model.complete(prompt + formated_paper)
        summary = remove_think_content(summary).strip() + '<br>\n'
        detail = (
            '<b>Abstract:</b> ' + paper['abstract'] + '<br>\n'
            '<b>URL:</b> ' + paper['url'] + '<br>\n'
            '<b>AlphaXiv:</b> ' + paper['url'].replace('arxiv', 'alphaxiv') + '<br>\n'
        )
        summaries.append(summary + detail)
    return summaries


def run_once(keywords, categories):
    today = date.today() - timedelta(days=1)
    yesterday = today - timedelta(days=1)

    date_from = yesterday.strftime('%Y-%m-%d')
    data_until = today.strftime('%Y-%m-%d')
    print('fetch from {} to {}.'.format(date_from, data_until))

    outputs = fetch_papers(date_from, data_until, categories, False)

    if outputs is None:
        return []

    filtered_papers = filter_papers(outputs, keywords)
    return filtered_papers


def post_msg_fwrite(content):
    with open('./tmp.md', 'w') as f:
        f.write(content)


def post_msg_qq_email(content):
    sender = os.getenv('SENDER')
    auth_code = os.getenv('AUTH_CODE')
    receivers = list(map(lambda x: x.strip(), os.getenv('RECEIVERS').split(',')))
    
    message = MIMEText(content, 'html', 'utf-8')
    message['From'] = formataddr(('Python自动邮件', sender))
    message['To'] = formataddr(('收件人', ','.join(receivers)))
    message['Subject'] = Header('{}-每日arXiv论文'.format(date.today()), 'utf-8')
    
    try:
        smtp_obj = smtplib.SMTP_SSL('smtp.qq.com', 465)
        smtp_obj.login(sender, auth_code)
        smtp_obj.sendmail(sender, receivers, message.as_string())
        print("邮件发送成功")
        smtp_obj.quit()
    except smtplib.SMTPException as e:
        print("邮件发送失败:", e)


if __name__ == '__main__':
    papers = run_once(KEYWORDS, CATEGORIES)
    print(f'fetch {len(papers)} papers after filtering.')

    if SMOKE_TEST:
        papers = papers[:2]
        print('test mode on.')

    post_msg = post_msg_qq_email
    if len(papers) == 0:
        post_msg('No papers found. Maybe today is Monday?')
    else:
        papers = deduplicate_papers(papers)
        print(f'Get {len(papers)} papers after deduplication.')
        summaries = summarize_papers(papers)
        all_msg = '\n\n'.join(
            f'<h2>Paper {i+1}</h2>\n' + summary for i, summary in enumerate(summaries))
        post_msg(
            '!注意! 内容由LLM整理, 请注意潜在错误 !注意!<br>\n' + all_msg
        )
