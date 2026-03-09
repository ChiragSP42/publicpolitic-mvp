from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import requests
import fitz  # PyMuPDF

def get_city_council_pdf_text(url):
    # 1. Fetch the page using Playwright to render the JavaScript
    print("Loading page and waiting for table to populate...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url)
        
        # Wait for the table body to actually populate with rows
        # We wait for at least one 'tr' to appear inside the tbody
        page.wait_for_selector('#upcomingMeetingsTable tbody tr', timeout=10000)
        
        # Get the fully rendered HTML and close the browser
        html_content = page.content()
        browser.close()

    # Now use BeautifulSoup on the fully rendered HTML
    soup = BeautifulSoup(html_content, 'html.parser')
    
    # 2. Locate the specific table
    container = soup.find('div', id='upcomingMeetingsContent')
    table = None
    if container:
        table = container.find('table', id='upcomingMeetingsTable')
    # body = table.find("tbody")
    
    pdf_url = None
    
    # 3. Find the "Planning Commission" row and extract the href
    if table:
        print("Parsing table rows...")
        for row in table.find_all('tr'):
            # Get all text in the row to see if our target phrase is inside
            if "Planning Commission" in row.get_text(): 
                link_tag = row.find('a', href=True)
                if link_tag:
                    # urljoin intelligently combines the base url and the href 
                    # regardless of if the href is relative (/Public/...) or absolute (https://...)
                    pdf_url = urljoin(url, str(link_tag['href'])) 
                    break

    if not pdf_url:
        return "Could not find the Planning Commission PDF link."

    print(f"Found PDF URL: {pdf_url}\nDownloading and extracting text...")

    # 4. Download and Read PDF
    # (Since the PDF itself is a static file, we can still safely use 'requests' here)
    pdf_response = requests.get(pdf_url)
    with fitz.open(stream=pdf_response.content, filetype="pdf") as doc:
        text = ""
        for page in doc:
            text += str(page.get_text())
            
    return text

# Usage
agenda_text = get_city_council_pdf_text("https://lasvegas.primegov.com/public/portal/")
print(agenda_text[:1000]) # Printing just the first 1000 characters to verify