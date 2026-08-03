import requests, re

res = requests.get('https://www.practo.com/ahmedabad/clinic/ivories-laser-dental-clinic-dental-implant-center-vastrapur', headers={'User-Agent':'Mozilla/5.0'})
m = re.search(r'window\.__REDUX_STATE__=(.*?)<', res.text)
if m:
    data = m.group(1)
    print('Phone:', re.findall(r'"number":"([^"]+)"', data))
    print('Addr:', re.findall(r'"street_address":"([^"]+)"', data))
    print('Loc:', re.findall(r'"locality":"([^"]+)"', data))
else:
    print('No match')
