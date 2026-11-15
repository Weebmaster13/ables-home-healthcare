import urllib.request

pages = ['/', '/care', '/contact', '/brochure', '/administrator', '/pricing-payment', '/service-detail/personal-care']

for url in pages:
    try:
        html = urllib.request.urlopen(f'http://127.0.0.1:5001{url}').read().decode()
        print(f'=== {url} ===')
        print('  Status: 200 OK')
        print('  Phone 301-326-2225:', '301-326-2225' in html)
        print('  tel:+13013262225:', 'tel:+13013262225' in html)
        print('  Fax 301-326-2270:', '301-326-2270' in html)
        print('  After Hours:', 'After Hours: 301-455-3030' in html)
        print()
    except Exception as e:
        print(f'=== {url} ===')
        print(f'  Error: {e}')
        print()