"""Lê os preços dos produtos de produtos.json no Pingo Doce e no Continente e grava precos.json.

Corre no GitHub Actions (ver .github/workflows/precos.yml). Só consulta as páginas dos produtos
indicados em produtos.json, uma vez por dia, com uma pausa entre pedidos.
Se um preço não for encontrado, mantém o último preço conhecido e marca-o como antigo.
"""
import json, re, sys, time, random, datetime, pathlib

import requests
from bs4 import BeautifulSoup

RAIZ = pathlib.Path(__file__).resolve().parent.parent
PRODUTOS = RAIZ / 'produtos.json'
SAIDA = RAIZ / 'precos.json'
CABECALHOS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36',
    'Accept-Language': 'pt-PT,pt;q=0.9',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
}
LOJAS = {'pingodoce': 'pingodoce.pt', 'continente': 'continente.pt'}


def num(v):
    """'1,29 €' / '1.29' / 1.29 → 1.29"""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v) if v > 0 else None
    s = str(v).replace('\xa0', ' ').replace('€', '').strip()
    m = re.search(r'\d+(?:[.,]\d+)?', s)
    if not m:
        return None
    x = float(m.group(0).replace(',', '.'))
    return x if x > 0 else None


def do_jsonld(soup):
    for sc in soup.find_all('script', type='application/ld+json'):
        try:
            dados = json.loads(sc.string or '')
        except Exception:
            continue
        pilha = dados if isinstance(dados, list) else [dados]
        while pilha:
            d = pilha.pop()
            if isinstance(d, list):
                pilha.extend(d); continue
            if not isinstance(d, dict):
                continue
            if '@graph' in d:
                pilha.extend(d['@graph'])
            if 'Product' in str(d.get('@type', '')):
                ofertas = d.get('offers')
                for o in (ofertas if isinstance(ofertas, list) else [ofertas]):
                    if isinstance(o, dict):
                        p = num(o.get('price') or o.get('lowPrice'))
                        if p:
                            return p, d.get('name')
    return None, None


def extrair(html):
    """Devolve (preço, nome) a partir do HTML da página de um produto."""
    soup = BeautifulSoup(html, 'html.parser')
    nome = (soup.find('h1').get_text(' ', strip=True) if soup.find('h1') else None)
    # 1) dados estruturados (schema.org)
    p, n = do_jsonld(soup)
    if p:
        return p, n or nome
    # 2) meta tags
    for sel in ['meta[itemprop=price]', 'meta[property="product:price:amount"]', '[itemprop=price][content]']:
        el = soup.select_one(sel)
        if el and num(el.get('content')):
            return num(el.get('content')), nome
    # 3) preço de venda das lojas Salesforce (Continente e Pingo Doce usam esta plataforma)
    for sel in ['.prices .sales .value[content]', '.price .sales .value[content]', '.sales .value[content]',
                '.ct-price-formatted', '.prices .sales .value', '.product-price .sales']:
        el = soup.select_one(sel)
        if el:
            p = num(el.get('content') or el.get_text(' ', strip=True))
            if p:
                return p, nome
    m = re.search(r'"sales"\s*:\s*\{[^{}]*?"value"\s*:\s*"?(\d+(?:\.\d+)?)', html)
    if m and num(m.group(1)):
        return num(m.group(1)), nome
    # 4) Continente: blocos de dados do produto (data-product-detail / dataLayer / "price":...)
    for el in soup.select('[data-product-detail], [data-product], [data-gtm-info], [data-gtm]'):
        for at in ('data-product-detail', 'data-product', 'data-gtm-info', 'data-gtm'):
            v = el.get(at)
            if v and '"price' in v:
                m = re.search(r'"price"\s*:\s*"?(\d+(?:[.,]\d+)?)', v)
                if m and num(m.group(1)):
                    return num(m.group(1)), nome
    for el in soup.select('.ct-price-value, .pwc-tile--price-primary, .prices-wrapper .value, .ct-pdp--price .value'):
        p = num(el.get('content') or el.get_text(' ', strip=True))
        if p:
            return p, nome
    m = re.search(r'"price"\s*:\s*"?(\d+[.,]\d{1,2})"?\s*,\s*"(?:currency|priceCurrency)"', html)
    if m and num(m.group(1)):
        return num(m.group(1)), nome
    return None, nome


def preco_de_json(dados):
    """Procura um preço de venda num JSON (resposta da loja)."""
    pilha = [dados]
    while pilha:
        d = pilha.pop()
        if isinstance(d, list):
            pilha.extend(d); continue
        if not isinstance(d, dict):
            continue
        s = d.get('sales')
        if isinstance(s, dict) and num(s.get('value')):
            return num(s.get('value'))
        for k in ('salesPrice', 'price', 'unitPrice'):
            v = d.get(k)
            if isinstance(v, (int, float, str)) and num(v) and re.fullmatch(r'\s*\d+(?:[.,]\d+)?\s*€?\s*', str(v)):
                return num(v)
        pilha.extend(v for v in d.values() if isinstance(v, (dict, list)))
    return None


def continente_api(url):
    """Continente: pede o preço diretamente à loja (plataforma Salesforce), pelo número do produto no fim do link."""
    m = re.search(r'-(\d{5,})\.html', url)
    if not m:
        return None, None
    pid = m.group(1)
    base = 'https://www.continente.pt/on/demandware.store/Sites-continente-Site/default/'
    for rota in (f'Product-Variation?pid={pid}&quantity=1', f'Product-ShowQuickView?pid={pid}'):
        try:
            r = requests.get(base + rota, headers={**CABECALHOS, 'Accept': 'application/json, text/html;q=0.9',
                                                   'X-Requested-With': 'XMLHttpRequest', 'Referer': url}, timeout=30)
            if r.status_code != 200:
                continue
            try:
                j = r.json()
            except Exception:
                p, n = extrair(r.text)
                if p:
                    return p, n
                continue
            p = preco_de_json(j.get('product', j) if isinstance(j, dict) else j)
            if p:
                prod = j.get('product', {}) if isinstance(j, dict) else {}
                return p, prod.get('productName') or prod.get('name')
            html = j.get('renderedTemplate') if isinstance(j, dict) else None
            if html:
                p, n = extrair(html)
                if p:
                    return p, n
        except Exception:
            continue
    return None, None


def bloqueado(html):
    t = html[:20000].lower()
    return any(x in t for x in ('captcha', 'access denied', 'request blocked', 'are you a robot', 'pardon our interruption', 'cf-challenge'))


def com_requests(url):
    r = requests.get(url, headers=CABECALHOS, timeout=30)
    r.raise_for_status()
    return r.text


_navegador = None
def com_playwright(url):
    """Plano B: abre a página num Chrome sem janela (para páginas que só mostram o preço com JavaScript)."""
    global _navegador
    from playwright.sync_api import sync_playwright
    if _navegador is None:
        pw = sync_playwright().start()
        _navegador = pw.chromium.launch()
    pg = _navegador.new_page(user_agent=CABECALHOS['User-Agent'], locale='pt-PT', viewport={'width': 1366, 'height': 900})
    respostas = []
    m = re.search(r'-(\d{5,})\.html', url)
    pid = m.group(1) if m else '§'
    def guardar(resp):
        try:
            # só respostas deste produto (não as de produtos recomendados)
            if pid in resp.url and 'json' in (resp.headers.get('content-type') or '') and len(respostas) < 40:
                respostas.append(resp.json())
        except Exception:
            pass
    pg.on('response', guardar)
    try:
        pg.goto(url, wait_until='domcontentloaded', timeout=45000)
        for txt in ('Aceitar todos', 'Aceitar', 'Accept all', 'Concordo'):
            try:
                pg.get_by_role('button', name=txt).first.click(timeout=1500); break
            except Exception:
                pass
        try:
            pg.wait_for_selector('.ct-price-formatted, .prices .sales .value, [itemprop=price], .ct-price-value, .pwc-tile--price-primary', timeout=15000)
        except Exception:
            pass
        try:
            pg.wait_for_load_state('networkidle', timeout=10000)
        except Exception:
            pass
        com_playwright.respostas = respostas
        return pg.content()
    finally:
        pg.close()
com_playwright.respostas = []


def ler(url):
    erro = ''
    html = ''
    try:
        html = com_requests(url)
        p, n = extrair(html)
        if p:
            return p, n, ''
        erro = 'preço não encontrado na página'
    except Exception as e:
        erro = f'{type(e).__name__}: {e}'[:200]
    if 'continente.pt' in url:
        p, n = continente_api(url)
        if p:
            return p, n, ''
        erro += ' | API Continente sem preço'
    try:
        html = com_playwright(url)
        p, n = extrair(html)
        if not p:
            for j in com_playwright.respostas:
                p = preco_de_json(j)
                if p:
                    break
        if p:
            return p, n, ''
        erro = 'preço não encontrado na página (também com o browser)'
    except Exception as e:
        erro = f'{erro} | browser: {type(e).__name__}: {e}'[:300]
    if html and bloqueado(html):
        erro += ' — o site BLOQUEOU o acesso (página de verificação/robô)'
    elif html:
        t = re.search(r'<title>(.*?)</title>', html, re.S | re.I)
        erro += f' — página: "{(t.group(1).strip() if t else "?")[:60]}", tem "€": {"sim" if "€" in html else "não"}'
    return None, None, erro


def main():
    if not PRODUTOS.exists():
        print('Falta o ficheiro produtos.json'); return 1
    lista = json.loads(PRODUTOS.read_text(encoding='utf-8'))
    lista = lista.get('produtos', lista) if isinstance(lista, dict) else lista
    antigo = {}
    if SAIDA.exists():
        try:
            antigo = json.loads(SAIDA.read_text(encoding='utf-8')).get('precos', {})
        except Exception:
            antigo = {}
    hoje = datetime.date.today().isoformat()
    precos, ok, falhas = {}, 0, 0
    urls = []
    for prod in lista:
        for loja, dominio in LOJAS.items():
            u = (prod.get(loja) or '').strip()
            if u.startswith('https://') and dominio in u and u not in urls:
                urls.append(u)
    print(f'{len(urls)} página(s) de produtos para ler')
    for i, u in enumerate(urls):
        p, n, erro = ler(u)
        if p:
            precos[u] = {'preco': round(p, 2), 'nome': n or '', 'data': hoje}
            ok += 1
            print(f'OK   {p:7.2f} €  {u}')
        else:
            velho = antigo.get(u) or {}
            precos[u] = {**velho, 'erro': erro}
            if velho.get('preco'):
                precos[u]['antigo'] = True
            falhas += 1
            print(f'FALHA {u}  →  {erro}')
        if i < len(urls) - 1:
            time.sleep(random.uniform(2, 5))       # pausa entre pedidos, para não sobrecarregar os sites
    SAIDA.write_text(json.dumps({'atualizado': datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%MZ'), 'precos': precos},
                                ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'\n{ok} preço(s) lidos, {falhas} falha(s)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
