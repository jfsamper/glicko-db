"""Administrative news publishing routes."""
from flask import flash, redirect, render_template, request, url_for

from services.i18n import TRANSLATIONS
from services.news_service import delete_article, get_article, list_articles, save_article, tag_options


def _admin_routes():
    from routes import admin
    return admin


def register_news_routes(admin_bp):
    admin_bp.add_url_rule("/admin/news", endpoint="admin_news", view_func=admin_news, methods=["GET"])
    admin_bp.add_url_rule("/admin/news/new", endpoint="admin_news_new", view_func=admin_news_new, methods=["GET", "POST"])
    admin_bp.add_url_rule("/admin/news/<int:article_id>/edit", endpoint="admin_news_edit", view_func=admin_news_edit, methods=["GET", "POST"])
    admin_bp.add_url_rule("/admin/news/<int:article_id>/delete", endpoint="admin_news_delete", view_func=admin_news_delete, methods=["POST"])


def _tag_form_data():
    tags = []
    for tag_type, entity_id in zip(request.form.getlist("tag_type"), request.form.getlist("entity_id")):
        if tag_type and entity_id:
            tags.append({"tag_type": tag_type, "entity_id": entity_id})
    return tags


def _render_form(admin, lang, article=None, error=None):
    conn = admin.get_db()
    try:
        options = tag_options(conn)
    finally:
        conn.close()
    return render_template(
        "admin/news_form.html",
        lang=lang,
        translations=TRANSLATIONS[lang],
        article=article or {"title": "", "body": "", "is_published": 0, "tags": []},
        tag_options=options,
        error=error,
    )


def admin_news():
    admin = _admin_routes()
    permission_error = admin.require_permission("operator")
    if permission_error is not None:
        return permission_error
    lang = admin.get_language(request.args.get("lang"))
    conn = admin.get_db()
    try:
        articles = list_articles(conn)
    finally:
        conn.close()
    return render_template("admin/news.html", lang=lang, translations=TRANSLATIONS[lang], articles=articles)


def _save_news(article_id=None):
    admin = _admin_routes()
    permission_error = admin.require_permission("operator")
    if permission_error is not None:
        return permission_error
    lang = admin.get_language(request.args.get("lang"))
    conn = admin.get_db()
    try:
        saved_id = save_article(
            conn,
            request.form.get("title"),
            request.form.get("body"),
            request.form.get("is_published") == "1",
            _tag_form_data(),
            article_id=article_id,
        )
    except ValueError as exc:
        conn.rollback()
        article = {"title": request.form.get("title", ""), "body": request.form.get("body", ""), "is_published": int(request.form.get("is_published") == "1"), "tags": _tag_form_data()}
        return _render_form(admin, lang, article, str(exc))
    finally:
        conn.close()
    admin.log_admin_action("news_article_saved", "news", {"article_id": saved_id}, user_id=admin.session.get("user_id"))
    flash(TRANSLATIONS[lang]["success"])
    return redirect(url_for("admin_news", lang=lang))


def admin_news_new():
    admin = _admin_routes()
    permission_error = admin.require_permission("operator")
    if permission_error is not None:
        return permission_error
    lang = admin.get_language(request.args.get("lang"))
    if request.method == "POST":
        return _save_news()
    return _render_form(admin, lang)


def admin_news_edit(article_id):
    admin = _admin_routes()
    permission_error = admin.require_permission("operator")
    if permission_error is not None:
        return permission_error
    lang = admin.get_language(request.args.get("lang"))
    conn = admin.get_db()
    try:
        article = get_article(conn, article_id)
    finally:
        conn.close()
    if article is None:
        flash(TRANSLATIONS[lang]["error"])
        return redirect(url_for("admin_news", lang=lang))
    if request.method == "POST":
        return _save_news(article_id)
    return _render_form(admin, lang, article)


def admin_news_delete(article_id):
    admin = _admin_routes()
    permission_error = admin.require_permission("operator")
    if permission_error is not None:
        return permission_error
    lang = admin.get_language(request.args.get("lang"))
    conn = admin.get_db()
    try:
        delete_article(conn, article_id)
    except ValueError as exc:
        conn.rollback()
        flash(f"{TRANSLATIONS[lang]['error']}: {exc}")
    else:
        admin.log_admin_action("news_article_deleted", "news", {"article_id": article_id}, user_id=admin.session.get("user_id"))
        flash(TRANSLATIONS[lang]["success"])
    finally:
        conn.close()
    return redirect(url_for("admin_news", lang=lang))
