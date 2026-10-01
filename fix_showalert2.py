with open('frontend/static/js/api.js', 'r', encoding='utf-8') as f:
    content = f.read()

content = content.replace('container.innerHTML = <div class="alert alert-" role="alert"></div>;', 'container.innerHTML = <div class="alert alert-" role="alert"></div>;')

with open('frontend/static/js/api.js', 'w', encoding='utf-8') as f:
    f.write(content)
