import requests
from bs4 import BeautifulSoup
import json
import os
import re
import io
import csv
import urllib3

# Suppress the insecure request warnings since Granicus S3 uses mismatched SSL certs
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

try:
    import pypdf
except ImportError:
    pypdf = None

def clean_text(html_content):
    soup = BeautifulSoup(html_content, 'html.parser')
    for script in soup(["script", "style"]):
        script.extract()
    text = soup.get_text(separator=' ', strip=True)
    text = re.sub(r'\s+', ' ', text)
    return text

def extract_pdf_text(pdf_bytes):
    if not pypdf:
        return "ERROR_PYPDF_MISSING"
    try:
        pdf_file = io.BytesIO(pdf_bytes)
        reader = pypdf.PdfReader(pdf_file)
        text = ""
        for i, page in enumerate(reader.pages[:10]):
            extracted = page.extract_text()
            if extracted:
                text += f"[Page {i+1}] {extracted}\n"
        text = re.sub(r'\s+', ' ', text)
        return text.strip()
    except Exception as e:
        return f"ERROR_PDF_PARSE: {str(e)}"

def get_subscribers():
    csv_url = os.environ.get('ALERTS_CSV_URL')
    if not csv_url:
        print("No ALERTS_CSV_URL found. Skipping alerts.")
        return []
    try:
        res = requests.get(csv_url, timeout=10)
        if res.status_code != 200:
            return []
        
        subs = []
        reader = csv.reader(res.text.splitlines())
        headers = next(reader, None) # skip header
        for row in reader:
            email = None
            topics = ""
            for cell in row:
                if "@" in cell and "." in cell:
                    email = cell.strip()
                elif len(cell) > len(topics) and cell != email:
                    topics = cell.strip()
            if email and topics:
                subs.append({"email": email, "topics": topics})
        return subs
    except Exception as e:
        print(f"Error fetching CSV: {e}")
        return []

import urllib.parse

def evaluate_alerts_and_summarize(text, meeting_name, matched_topics, api_key):
    # This single LLM call generates the summary
    url = "https://api.openai.com/v1/responses"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }
    
    system_prompt = "You are a non-partisan civic watchdog. Read the agenda and extract the 3-5 most impactful items for local residents."
    prompt = f"""Please read the following agenda and extract the 3-5 most important items. For each item, extract the physical address and the APN (Assessor's Parcel Number) if they are mentioned.

Agenda Text:
{text[:8000]}
"""
    
    if matched_topics:
        topics_str = ", ".join(matched_topics)
        prompt += f"\nCRITICAL INSTRUCTION: The following tracked keywords were explicitly found in this agenda: {topics_str}. You MUST include a dedicated summary item for each of these keywords explaining their context in the meeting.\n"
        
    prompt += """
Output JSON exactly in this format:
{
  "summary_items": [
    {
      "text": "The plain text description of the agenda item...",
      "page_number": 2,
      "address": "341 Holly Street", 
      "apn": "496-035-01"
    }
  ]
}
Note: If an address or APN is not mentioned, set the field to null. If a page number is unknown, set it to null.
"""

    data = {
        "model": "gpt-4o-mini",
        "input": prompt,
        "store": True
    }
    
    try:
        res = requests.post(url, headers=headers, json=data, timeout=30)
        if res.status_code == 200:
            res_json = res.json()
            output_text = res_json["output"][0]["content"][0]["text"]
            # Extract JSON from the output block
            match = re.search(r'\{.*\}', output_text, re.DOTALL)
            if match:
                parsed = json.loads(match.group(0))
                
                # Build the HTML programmatically
                summary_html = "<ul>"
                items = parsed.get("summary_items", [])
                if not items:
                    summary_html += "<li>No major items found.</li>"
                    
                for item in items:
                    page_str = f" <strong style='color:#4ab58e;'>(Page {item.get('page_number')})</strong>" if item.get('page_number') else ""
                    summary_html += f"<li style='margin-bottom: 12px; line-height: 1.6;'>{item.get('text', '')}{page_str}"
                    addr = item.get("address")
                    apn = item.get("apn")
                    
                    if addr or apn:
                        summary_html += ' <div style="margin-top: 8px;">'
                        pill_style = "display:inline-block; margin-right:8px; margin-bottom:8px; padding:4px 10px; background:#e6f4ea; color:#4ab58e; text-decoration:none; border-radius:50px; font-size:11px; font-weight:600; border:1px solid #c2e7cc;"
                        if addr:
                            addr_encoded = urllib.parse.quote(addr)
                            summary_html += f'<a href="https://www.google.com/maps/search/?api=1&query={addr_encoded}+Laguna+Beach+CA" target="_blank" style="{pill_style}">&nbsp;📍 Google Maps&nbsp;</a>'
                            summary_html += f'<a href="https://gis.lagunabeachcity.net/Html5Viewer/index.html?viewer=LagunaBeachPublicGIS" target="_blank" style="{pill_style}">&nbsp;🗺️ City GIS&nbsp;</a>'
                        if apn:
                            summary_html += f'<a href="https://portal.laserfiche.com/Portal/Search.aspx?repo=r-1645a77d&searchcommand=%7BLF%3ALookin%3D%22%5CCommunity+Development%5CPlanning%22%7D+%26+%7B%5B%5D%3A%5BAPN%5D+%3D+%22{apn}%22%7D" target="_blank" style="{pill_style}">&nbsp;📄 Planning Files&nbsp;</a>'
                            summary_html += f'<a href="https://portal.laserfiche.com/Portal/Search.aspx?repo=r-1645a77d&searchcommand=%7BLF%3ALookin%3D%22%5CCommunity+Development%5CBuilding%22%7D+%26+%7B%5B%5D%3A%5BAPN%5D+%3D+%22{apn}%22%7D" target="_blank" style="{pill_style}">&nbsp;🏗️ Building Files&nbsp;</a>'
                        summary_html += '</div>'
                    summary_html += "</li>"
                summary_html += "</ul>"
                
                return summary_html
            else:
                return "Failed to parse JSON output."
        else:
            return f"Executive Summary temporarily unavailable (Status {res.status_code})."
    except Exception as e:
        return f"Executive Summary processing failed."

def send_brevo_email(email, meeting_name, meeting_date, agenda_url, topics, summary):
    brevo_key = os.environ.get('BREVO_API_KEY')
    if not brevo_key:
        print("No BREVO_API_KEY. Skipping email.")
        return
        
    url = "https://api.brevo.com/v3/smtp/email"
    headers = {
        "accept": "application/json",
        "api-key": brevo_key,
        "content-type": "application/json"
    }
    
    html_content = f"""
    <div style="font-family: 'Inter', Helvetica, sans-serif; max-width: 600px; margin: 0 auto; background: #ffffff; border-radius: 12px; overflow: hidden; border: 1px solid #e1e8ed; box-shadow: 0 4px 20px rgba(0,0,0,0.05);">
        <div style="background: #091c2b; padding: 30px; text-align: center;">
            <h1 style="color: #ffffff; margin: 0; font-size: 24px; font-weight: 800;">SoLaguna Civic Alerts</h1>
        </div>
        <div style="padding: 40px 30px;">
            <p style="color: #4a5568; font-size: 16px; line-height: 1.6;">Hi there,</p>
            <p style="color: #4a5568; font-size: 16px; line-height: 1.6;">Our AI watchdog just detected a match for your tracked topics <strong>({topics})</strong> in an upcoming city meeting!</p>
            
            <div style="background: #f7fafc; border-left: 4px solid #4ab58e; padding: 20px; margin: 25px 0; border-radius: 0 8px 8px 0;">
                <h3 style="color: #091c2b; margin: 0 0 5px 0;">{meeting_name}</h3>
                <p style="color: #4a5568; margin: 0 0 15px 0; font-size: 14px;">{meeting_date}</p>
                <p style="color: #091c2b; margin: 0; font-size: 15px; line-height: 1.6;">{summary}</p>
            </div>
            
            <a href="{agenda_url}" style="display: inline-block; background: #4ab58e; color: #ffffff; text-decoration: none; padding: 12px 25px; border-radius: 50px; font-weight: 600; font-size: 15px; margin-top: 10px;">View Official Agenda PDF</a>
        </div>
        <div style="background: #f1f5f9; padding: 20px; text-align: center; color: #718096; font-size: 13px;">
            <p style="margin: 0;">You are receiving this because you subscribed to Civic Alerts on SoLaguna.com.</p>
        </div>
    </div>
    """

    payload = {
        "sender": {"name": "SoLaguna AI Watchdog", "email": "alerts@solaguna.com"},
        "to": [{"email": email}],
        "subject": f"Civic Alert: Match found in upcoming {meeting_name}",
        "htmlContent": html_content
    }
    
    try:
        requests.post(url, headers=headers, json=payload, timeout=10)
        print(f"Sent alert to {email} for {meeting_name}")
    except Exception as e:
        print(f"Failed to send email to {email}: {e}")

def scrape_meetings():
    api_key = os.environ.get('OPENAI_API_KEY')
    subscribers = get_subscribers()
    
    # Load previously processed agendas to avoid spamming duplicates.
    # We load this from the tracked meetings_ai.json since it persists across GitHub Actions runs!
    try:
        with open('meetings_ai.json', 'r', encoding='utf-8') as f:
            old_meetings = json.load(f)
            sent_alerts = [m.get('agenda_url') for m in old_meetings if m.get('agenda_url')]
    except:
        sent_alerts = []

    url = "https://lagunabeachcity.granicus.com/ViewPublisher.php?view_id=3"
    headers = {"User-Agent": "Mozilla/5.0"}
    
    print("Fetching Granicus portal...")
    response = requests.get(url, headers=headers, verify=False)
    if response.status_code != 200:
        print(f"Failed to fetch {url}")
        return
        
    soup = BeautifulSoup(response.text, 'html.parser')
    tables = soup.find_all('table', class_='listingTable')
    
    database = []
    
    for table in tables:
        category = "Unknown"
        header_th = table.find('th', class_='listingTableHeader')
        if header_th:
            category = header_th.text.strip()
        else:
            prev_header = table.find_previous(['h2', 'caption', 'h3'])
            if prev_header:
                category = prev_header.text.strip()
                
        rows = table.find_all('tr')
        for row in rows:
            cols = row.find_all('td')
            if len(cols) >= 2:
                name = cols[0].text.strip()
                date = cols[1].text.strip()
                
                date = date.replace('\u00a0', ' ')
                date = re.sub(r'^\d{10}', '', date).strip()
                
                if not name or "Name" in name:
                    continue
                    
                links = row.find_all('a')
                agenda_url = None
                video_url = None
                
                for a in links:
                    text_link = a.text.strip().lower()
                    href = a.get('href', '')
                    if href.startswith('//'):
                        href = 'https:' + href
                    elif href.startswith('/'):
                        href = 'https://lagunabeachcity.granicus.com' + href
                        
                    if 'agenda' in text_link or 'documents' in text_link or 'agenda' in href.lower():
                        agenda_url = href
                    elif 'video' in text_link or 'video' in href.lower():
                        video_url = href
                        
                if not video_url:
                    for a in links:
                        onclick = a.get('onclick', '')
                        if 'MediaPlayer.php' in onclick:
                            try:
                                url_part = onclick.split("'")[1]
                                video_url = 'https://lagunabeachcity.granicus.com/' + url_part.lstrip('/')
                            except:
                                pass
                
                if len(database) >= 15:
                    continue
                    
                summary = "Agenda not available."
                extracted_text = ""
                
                if agenda_url:
                    try:
                        import time
                        time.sleep(3) # Delay to prevent OpenAI 429 Rate Limits
                        agenda_res = requests.get(agenda_url, headers=headers, timeout=30, verify=False)
                        if agenda_res.status_code == 200:
                            if agenda_res.content.lstrip().startswith(b'%PDF'):
                                extracted_text = extract_pdf_text(agenda_res.content)
                            else:
                                extracted_text = clean_text(agenda_res.text)
                                
                            if extracted_text.startswith("ERROR_"):
                                summary = f"System Error: {extracted_text}"
                            elif len(extracted_text.strip()) > 50:
                                # ONLY evaluate if we have API key
                                if api_key:
                                    # ONLY run alert evaluation if we haven't processed this agenda yet
                                    is_new_agenda = agenda_url not in sent_alerts
                                    matched_subs = []
                                    matched_topics = set()
                                    
                                    if is_new_agenda:
                                        text_lower = extracted_text.lower()
                                        for sub in subscribers:
                                            user_topics = [t.strip().lower() for t in sub['topics'].split(',')]
                                            for t in user_topics:
                                                if t and t in text_lower:
                                                    matched_subs.append(sub)
                                                    matched_topics.add(t)
                                                    break
                                    
                                    summary = evaluate_alerts_and_summarize(extracted_text, name, list(matched_topics), api_key)
                                    
                                    if is_new_agenda:
                                        for sub in matched_subs:
                                            send_brevo_email(sub['email'], name, date, agenda_url, sub['topics'], summary)
                                        # Mark as processed whether there was a match or not
                                        sent_alerts.append(agenda_url)
                                else:
                                    summary = "Executive Summary pending API configuration."
                            else:
                                summary = f"Agenda text too short or unreadable. Length: {len(extracted_text)}"
                    except Exception as e:
                        print(f"Error fetching agenda {agenda_url}: {e}")
                        summary = f"Network Error: {str(e)}"
                
                database.append({
                    "category": category,
                    "name": name,
                    "date": date,
                    "agenda_url": agenda_url,
                    "video_url": video_url,
                    "summary": summary,
                    "seo_text": extracted_text[:500]
                })
                
                if len(database) >= 15:
                    break
                    
        if len(database) >= 15:
            break
            
    with open('meetings_ai.json', 'w', encoding='utf-8') as f:
        json.dump(database, f, indent=2)
        
    print(f"Successfully scraped and processed {len(database)} meetings.")

if __name__ == "__main__":
    scrape_meetings()
