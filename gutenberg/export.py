#!/usr/bin/env python
# -*- coding: utf-8 -*-
# vim: ai ts=4 sts=4 et sw=4 nu

from __future__ import (unicode_literals, absolute_import,
                        division, print_function)
import os
import json

import bs4
from bs4 import BeautifulSoup
from path import path
from jinja2 import Environment, PackageLoader

import gutenberg
from gutenberg import logger, XML_PARSER
from gutenberg.utils import (FORMAT_MATRIX, main_formats_for,
                             get_list_of_filtered_books)
from gutenberg.database import Book, Format, BookFormat, Author
from gutenberg.iso639 import language_name

jinja_env = Environment(loader=PackageLoader('gutenberg', 'templates'))


def book_name_for_fs(book):
    return book.title.strip().replace('/', '-')
jinja_env.filters['book_name_for_fs'] = book_name_for_fs


def tmpl_path():
    return os.path.join(path(gutenberg.__file__).parent, 'templates')


def get_list_of_all_languages():
    return list(set(list([b.language for b in Book.select(Book.language)])))


def export_all_books(static_folder,
                     download_cache,
                     languages=[],
                     formats=[],
                     only_books=[]):

    # ensure dir exist
    path(static_folder).mkdir_p()

    books = get_list_of_filtered_books(languages=languages,
                                       formats=formats,
                                       only_books=only_books)

    sz = len(list(books))
    logger.debug("\tFiltered book collection size: {}".format(sz))

    def nb_by_fmt(fmt):
        return sum([1 for book in books
                    if BookFormat.select(BookFormat, Book, Format)
                                 .join(Book).switch(BookFormat)
                                 .join(Format)
                                 .where(Book.id == book.id)
                                 .where(Format.mime == FORMAT_MATRIX.get(fmt))
                                 .count()])

    logger.debug("\tFiltered book collection, PDF: {}".format(nb_by_fmt('pdf')))
    logger.debug("\tFiltered book collection, ePUB: {}".format(nb_by_fmt('epub')))
    logger.debug("\tFiltered book collection, HTML: {}".format(nb_by_fmt('html')))

    # export to JSON helpers
    export_to_json_helpers(books=books,
                           static_folder=static_folder,
                           languages=languages,
                           formats=formats)

    # copy CSS/JS/* to static_folder
    src_folder = tmpl_path()
    for fname in ('css', 'js', 'jquery', 'favicon.ico', 'favicon.png',
                  'jquery-ui', 'datatables'):
        src = os.path.join(src_folder, fname)
        dst = os.path.join(static_folder, fname)
        if not path(fname).ext:
            path(dst).rmtree_p()
            path(src).copytree(dst)
        else:
            path(src).copyfile(dst)

    # export homepage
    template = jinja_env.get_template('index.html')
    context = {'show_books': True}
    with open(os.path.join(static_folder, 'Home.html'), 'w') as f:
        f.write(template.render(**context))

    # export to HTML
    cached_files = os.listdir(download_cache)
    for book in books:
        export_book_to(book=book,
                       static_folder=static_folder,
                       download_cache=download_cache,
                       cached_files=cached_files,
                       languages=languages,
                       formats=formats)


def article_name_for(book, cover=False):
    cover = "_cover" if cover else ""
    title = book_name_for_fs(book)
    return "{title}{cover}.{id}.html".format(title=title, cover=cover, id=book.id)


def archive_name_for(book, format):
    return "{title}.{id}.{format}".format(
        title=book_name_for_fs(book),
        id=book.id, format=format)

def fname_for(book, format):
    return "{id}.{format}".format(id=book.id, format=format)


def html_content_for(book, static_folder, download_cache):

    html_fpath = os.path.join(download_cache, fname_for(book, 'html'))

    # is HTML file present?
    if not path(html_fpath).exists():
        logger.warn("Missing HTML content for #{} at {}"
                    .format(book.id, html_fpath))
        return None

    with open(html_fpath, 'r') as f:
        return f.read()


def update_html_for_static(book, html_content):
    # update all <img> links from images/xxx.xxx to {id}_xxx.xxx
    soup = BeautifulSoup(html_content, XML_PARSER)
    for img in soup.findAll('img'):
        if 'src' in img.attrs:
            img.attrs['src'] = img.attrs['src'].replace('images/', '{id}_'.format(book.id))

    # Add the title
    soup.title.string = book.title

    start_of_text = '*** START OF THIS PROJECT GUTENBERG EBOOK'
    end_of_text = '*** END OF THIS PROJECT GUTENBERG EBOOK'

    patterns = [
        ("*** START OF THIS PROJECT GUTENBERG EBOOK",
         "*** END OF THIS PROJECT GUTENBERG EBOOK"),
        ("=========================================================================",
         "——————————————————————————-"),
        ("—————————————————-", "Encode an ISO 8859/1 Etext into LaTeX or HTML"),
        ("Project Gutenberg Etext", "End of Project Gutenberg Etext"),
        ("***START OF THE PROJECT GUTENBERG",
         "***END OF THE PROJECT GUTENBERG EBOOK"),
        ("<><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><>",
         "<><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><><>"),
        ("Text encoding is iso-8859-1", "Fin de Project Gutenberg Etext"),
        ("COPYRIGHT PROTECTED ETEXTS*END*",
         "==========================================================="),
        ("Nous remercions la Bibliothèque Nationale de France qui a mis à",
         "The Project Gutenberg Etext of"),
        ("Nous remercions la Bibliothèque Nationale de France qui a mis à",
         "End of The Project Gutenberg EBook"),
        ("*** START OF THE PROJECT GUTENBERG EBOOK",
         "*** END OF THE PROJECT GUTENBERG EBOOK"),
        ("***START OF THE PROJECT GUTENBERG EBOOK",
         "***END OF THE PROJECT GUTENBERG EBOOK"),
    ]


    body = soup.find('body')
    for start_of_text, end_of_text in patterns:
        if start_of_text in body.text and end_of_text in body.text:
            remove = True
            for child in body.children:

                if isinstance(child, bs4.NavigableString):
                    continue

                if end_of_text in getattr(child, 'text', ''):
                    remove = True

                if start_of_text in getattr(child, 'text', ''):
                    child.decompose()
                    remove = False

                if remove:
                    child.decompose()
            break

    # build infobox
    infobox = jinja_env.get_template('book_infobox.html')
    infobox_html = infobox.render({'book': book})
    info_soup = BeautifulSoup(infobox_html)
    body.insert(0, info_soup.find('div'))

    # if there is no charset, set it to utf8
    if not soup.encoding:
        utf = '<meta http-equiv="Content-Type" content="text/html; charset=UTF-8" />'
        # title = soup.find('title')
        # title.insert_before(utf)
        utf = '<head>{}'.format(utf)

        return soup.encode().replace(str('<head>'), str(utf))

    return soup.encode()


def cover_html_content_for(book):
    cover_img = path("{id}_cover.jpg")
    cover_img = str(cover_img) if cover_img.exists() else None
    context = {
        'book': book,
        'cover_img': cover_img,
        'formats': main_formats_for(book)
    }
    template = jinja_env.get_template('cover_article.html')
    return template.render(**context)


def export_book_to(book,
                   static_folder, download_cache,
                   cached_files, languages, formats):
    logger.info("\tExporting Book #{id}.".format(id=book.id))

    # actual book content, as HTML
    html = html_content_for(book=book,
                            static_folder=static_folder,
                            download_cache=download_cache)
    if html:
        article_fpath = os.path.join(static_folder, article_name_for(book))
        logger.info("\t\tExporting to {}".format(article_fpath))
        new_html = update_html_for_static(book=book, html_content=html)
        with open(article_fpath, 'w') as f:
            f.write(new_html)

    def symlink_from_cache(fname, dstfname=None):
        src = os.path.join(download_cache, fname)
        if dstfname is None:
            dstfname = fname
        dst = os.path.join(static_folder, dstfname)
        logger.info("\t\tSymlinking {}".format(dst))
        path(dst).unlink_p()
        path(src).symlink(dst)

    # associated files (images, etc)
    for fname in [fn for fn in cached_files
                  if fn.startswith("{}_".format(book.id))]:
        symlink_from_cache(fname)

    # other formats
    for format in formats:
        symlink_from_cache(fname_for(book, format),
                           archive_name_for(book, format))

    # book presentation article
    cover_fpath = os.path.join(static_folder,
                                 article_name_for(book=book, cover=True))
    logger.info("\t\tExporting to {}".format(cover_fpath))
    html = cover_html_content_for(book=book)
    with open(cover_fpath, 'w') as f:
        f.write(html.encode('utf-8'))


def export_to_json_helpers(books, static_folder, languages, formats):

    def dumpjs(col, fn, var='json_data'):
        with open(os.path.join(static_folder, fn), 'w') as f:
            f.write("var {var} = ".format(var=var))
            f.write(json.dumps(col))
            f.write(";")
            # json.dump(col, f)

    # all books sorted by popularity
    logger.info("\t\tDumping full_by_popularity.js")
    dumpjs([book.to_array()
            for book in books.order_by(Book.downloads.desc())],
           'full_by_popularity.js')

    # all books sorted by title
    logger.info("\t\tDumping full_by_title.js")
    dumpjs([book.to_array()
            for book in books.order_by(Book.title.asc())],
           'full_by_title.js')

    avail_langs = list(set([(language_name(b.language), b.language)
                            for b in books]))

    # language-specific collections
    for lang_name, lang in avail_langs:
        # by popularity
        logger.info("\t\tDumping lang_{}_by_popularity.js".format(lang))
        dumpjs([book.to_array()
                for book in books.where(Book.language == lang)
                                 .order_by(Book.downloads.desc())],
                'lang_{}_by_popularity.js'.format(lang))
        # by title
        logger.info("\t\tDumping lang_{}_by_title.js".format(lang))
        dumpjs([book.to_array()
                for book in books.where(Book.language == lang)
                                 .order_by(Book.title.asc())],
                'lang_{}_by_title.js'.format(lang))

    # author specific collections
    authors = Author.select().where(
        Author.gut_id << list(set([book.author.gut_id
                                   for book in books])))
    for author in authors:
        # by popularity
        logger.info("\t\tDumping auth_{}_by_popularity.js".format(author.gut_id))
        dumpjs([book.to_array()
                for book in books.where(Book.author == author)
                                 .order_by(Book.downloads.desc())],
                'auth_{}_by_popularity.js'.format(author.gut_id))
        # by title
        logger.info("\t\tDumping auth_{}_by_title.js".format(author.gut_id))
        dumpjs([book.to_array()
                for book in books.where(Book.author == author)
                                 .order_by(Book.title.asc())],
                'auth_{}_by_title.js'.format(author.gut_id))

    # authors list sorted by name
    logger.info("\t\tDumping authors.js")
    dumpjs([author.to_array()
            for author in authors.order_by(Author.last_name.asc(),
                                           Author.first_names.asc())],
                'authors.js', 'authors_json_data')

    # languages list sorted by code
    logger.info("\t\tDumping languages.js")
    dumpjs(sorted(avail_langs), 'languages.js', 'languages_json_data')
