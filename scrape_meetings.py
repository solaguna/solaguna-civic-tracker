import requests
from bs4 import BeautifulSoup
import json
import os
import re
import io
try:
    import pypdf
except ImportError:
    pypdf = None

def clean_text(html_content):
    """Extract clean text from HTML"""
    soup = BeautifulSoup(html_content, 'html.parser')
    for script in soup(["script", "style"]):
        script.extract()
    text = soup.get_text(separator=' ', strip=True)
    text = re.sub(r'\s+', ' ', text)
    return text

def extract_pdf_text(pdf_bytes):
    """Extract text from a PDF byte stream"""
    if not pypdf:
        return "PDF Parsing not available."
    try:
        pdf_file = io.BytesIO(pdf_bytes)
        reader = pypdf.PdfReader(pdf_file)
        text = ""
        # Read up to first 10 pages to save time/tokens
        for page in reader.pages[:10]:
            extracted = page.extract_text()
            if extracted:
                text += extracted + "\n"
        # Collapse multiple spaces/newlines
        text = re.sub(r'\s+', ' ', text)
        return text.strip()
    except Exception as e:
        return f"Failed to parse PDF: {str(e)}"

def generate_ai_summary(text):
    """
    Generate an AI summary using the OpenAI API.
    """
    api_key = os.environ.get('OPENAI_API_KEY')
    if not api_key:
        return "AI Summary: This meeting covered standard civic agenda items. (Configure API key for full AI digest.)"
        
    url = "https://api.openai.com/v1/responses"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }
    
    prompt = f"Provide a simple, digestible 2-3 sentence executive summary of the following city meeting agenda:\n\n{text[:8000]}"
    
    data = {
        "model": "gpt-5.4-mini",
        "input": prompt,
        "store": True
    }
    
    try:
        res = requests.post(url, headers=headers, json=data, timeout=30)
        if res.status_code == 200:
            res_json = res.json()
            return res_json["output"][0]["content"][0]["text"]
        else:
            return f"AI Summary generation failed (Status {res.status_code})."
    except Exception as e:
        return f"AI Summary failed: {str(e)}"

def scrape_meetings():
    url = "https://lagunabeachcity.granicus.com/ViewPublisher.php?view_id=3"
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
    }
    
    print("Fetching Granicus portal...")
    response = requests.get(url, headers=headers)
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
                # Remove leading timestamp if present (e.g. 1781222400June 11)
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
                        agenda_res = requests.get(agenda_url, headers=headers, timeout=10)
                        if agenda_res.status_code == 200:
                            # Check if the response is a PDF
                            if agenda_res.content.startswith(b'%PDF'):
                                extracted_text = extract_pdf_text(agenda_res.content)
                            else:
                                extracted_text = clean_text(agenda_res.text)
                                
                            if len(extracted_text.strip()) > 50:
                                summary = generate_ai_summary(extracted_text)
                    except Exception as e:
                        print(f"Error fetching agenda {agenda_url}: {e}")
                
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
