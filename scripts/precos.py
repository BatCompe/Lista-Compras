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
    return None, nome


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
    pg = _navegador.new_page(user_agent=CABECALHOS['User-Agent'], locale='pt-PT')
    try:
        pg.goto(url, wait_until='domcontentloaded', timeout=45000)
        pg.wait_for_timeout(3000)
        return pg.content()
    finally:
        pg.close()


def ler(url):
    erro = ''
    try:
        p, n = extrair(com_requests(url))
        if p:
            return p, n, ''
        erro = 'preço não encontrado na página'
    except Exception as e:
        erro = f'{type(e).__name__}: {e}'[:200]
    try:
        p, n = extrair(com_playwright(url))
        if p:
            return p, n, ''
        erro = 'preço não encontrado na página (também com o browser)'
    except Exception as e:
        erro = f'{erro} | browser: {type(e).__name__}: {e}'[:300]
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
