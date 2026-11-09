# As duas paginas, estaticas, servidas pelo nginx. O painel vai como
# precos.html, como era no GitHub Pages. Contexto de build: a raiz do repo.
FROM nginx:1.29.8-alpine
COPY hostinger/site/nginx.conf /etc/nginx/conf.d/default.conf
COPY index.html /usr/share/nginx/html/index.html
COPY painel_precos.html /usr/share/nginx/html/precos.html
