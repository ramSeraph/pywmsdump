import logging
import json

import requests
import xmltodict

from bs4 import BeautifulSoup
from urllib.parse import urljoin

logger = logging.getLogger(__name__)

# TODO: review and cleanup

def _get_layer_names(soup):
    lnames = []
    table = soup.find('table')
    tbody = table.find('tbody')
    
    for tr in tbody.find_all('tr'):
        tds = tr.find_all('td')
        layer_name = tds[2].text.strip()
        lnames.append(layer_name)
    return lnames

def _get_next_link_ajax(soup, script):
    span = soup.find('span', { 'class': 'paginator' })
    all_links = span.find_all('a')
    next_link = None
    for link in all_links:
        if link.get('disabled') == 'disabled':
            continue
        if link.text.strip() == '>':
            next_link = link
            break
    if next_link is None:
        return None, None
    next_link_id = next_link.get('id')
    next_url = None
    lines = []
    lines.extend(script.split(';'))
    wicket_prefix = '(function(){Wicket.Ajax.ajax('
    for line in lines:
        if line.startswith(wicket_prefix):
            line = line[len(wicket_prefix):]
            line = line[:-len(')')]
            data = json.loads(line)
            if data['c'] == next_link_id:
                next_url = data['u']
                break
    return next_url, next_link_id


def _get_next_link(soup):
    span = soup.find('span', { 'class': 'paginator' })
    all_links = span.find_all('a')
    next_link = None
    for link in all_links:
        if link.get('disabled') == 'disabled':
            continue
        if link.text.strip() == '>':
            next_link = link
            break
    if next_link is None:
        return None, None
    next_link_id = next_link.get('id')
    next_url = None
    lines = []
    scripts = soup.find_all('script')
    for script in scripts:
        lines.extend(script.text.split('\n'))
    wicket_prefix = 'Wicket.Ajax.ajax('
    for line in lines:
        if line.startswith(wicket_prefix):
            line = line[len(wicket_prefix):]
            line = line[:-len(');;')]
            data = json.loads(line)
            if data['c'] == next_link_id:
                next_url = data['u']
                break
    return next_url, next_link_id


def _parse_page(resp_text):
    soup = BeautifulSoup(resp_text, 'lxml')
    lnames = _get_layer_names(soup)
    next_link, next_link_id = _get_next_link(soup)
    return lnames, next_link, next_link_id


def _parse_page_ajax(resp_text):
    data = xmltodict.parse(resp_text)
    all_text = '\n'.join([e['#text'] for e in data['ajax-response']['component']])
    soup = BeautifulSoup(all_text, 'lxml')
    lnames = _get_layer_names(soup)
    next_link, next_link_id = _get_next_link_ajax(soup, data['ajax-response']['evaluate'])
    return lnames, next_link, next_link_id



def _get_preview_url(resp_text):
    soup = BeautifulSoup(resp_text, 'lxml')
    links = soup.find_all('a')     
    preview_link = None
    for link in links:
        if link.text.strip() == 'Layer Preview':
            preview_link = link
    if preview_link is None:
        raise Exception('no preview link')

    href = preview_link.get('href')
    parts = href.split('?')
    sub_parts = parts[0].split(';')
    if len(sub_parts) > 1:
        cookie_piece = sub_parts[1]
        cparts = cookie_piece.split('=')
        cookie_dict = { cparts[0]: cparts[1] }
    else:
        cookie_dict = {}

    if len(parts) > 1:
        base_url = sub_parts[0] + '?' + parts[1]
    else:
        base_url = sub_parts[0]
    return base_url, cookie_dict

def get_layer_list_from_page(url, **req_args):
    all_lnames = []
    session = requests.session()

    # start at main page ( for cookies?)
    logger.info('getting main page')
    resp = session.get(url, **req_args)
    if not resp.ok:
        raise Exception('unable to access geoserver page')

    logger.info('getting web page')
    new_url = urljoin(resp.url, 'web/')
    resp = session.get(new_url, **req_args)
    if not resp.ok:
        raise Exception('unable to get web page')

    # extract preview url
    preview_url, cookies = _get_preview_url(resp.text)
    preview_url = urljoin(new_url, preview_url)

    # go to preview page
    pno = 1
    logger.info(f'getting preview page {pno}')
    resp = session.get(preview_url, **req_args)
    if not resp.ok:
        raise Exception('unable to get preview page')
    curr_url = resp.url
    wicket_base_url = '/'.join(preview_url.split('/')[5:])
    headers = {
        'Referer': curr_url,
        'Wicket-Ajax': 'true',
        'Wicket-Ajax-Baseurl': wicket_base_url,
        'X-Requested-With': 'XMLHttpRequest',
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
    }

    all_lnames = []

    # get layer list and next link
    lnames, next_url, next_link_id = _parse_page(resp.text)
    for lname in lnames:
        logger.debug(f'layer: {lname}')
    all_lnames.extend(lnames)
    pno += 1

    while next_link_id is not None:

        next_url = urljoin(curr_url, next_url)
        new_headers = {}
        new_headers.update(headers)
        new_headers.update({ 'Wicket-Focusedelementid': next_link_id })
        logger.info(f'getting preview page {pno}')
        resp = session.get(next_url, headers=new_headers, **req_args)
        if not resp.ok:
            raise Exception(f'unable to get next page {pno}')
        lnames, next_url, next_link_id = _parse_page_ajax(resp.text)
        for lname in lnames:
            logger.debug(f'layer: {lname}')
        all_lnames.extend(lnames)
        pno += 1
    return all_lnames
