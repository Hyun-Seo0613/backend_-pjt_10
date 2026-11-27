from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.core.paginator import Paginator
from django.views.decorators.http import (
    require_http_methods,
    require_safe,
    require_POST,
)
from django.contrib.auth.decorators import login_required

from accounts.models import Category
from .models import Book, Thread, Comment
from .forms import ThreadForm, CommentForm
from .utils import (
    generate_image_with_openai,
)
from .recommendations import get_recommendations


# Index 페이지
BOOKS_PER_PAGE = 24


@require_safe
def index(request):
    books = Book.objects.select_related('category').all()
    paginator = Paginator(books.order_by('title'), BOOKS_PER_PAGE)
    page = paginator.get_page(1)
    context = {
        'categories': Category.objects.all().order_by('name'),
        'books': page.object_list,
        'featured_books': books.order_by('-customer_review_rank', 'title')[:4],
        'book_count': paginator.count,
        'has_more_books': page.has_next(),
        'next_page': page.next_page_number() if page.has_next() else None,
    }
    return render(request, 'books/index.html', context)

# 장르별 필터링
@require_safe
def filter_category(request):
    category_name = request.GET.get('category', '').strip()
    books = Book.objects.select_related('category').all()
    if category_name:
        books = books.filter(category__name=category_name)

    paginator = Paginator(books.order_by('title'), BOOKS_PER_PAGE)
    page = paginator.get_page(request.GET.get('page', 1))
    data = {
        'books': [
            {
                'id': book.pk,
                'title': book.title,
                'author': book.author,
                'publisher': book.publisher,
                'customer_review_rank': book.customer_review_rank,
                'description': (
                    f'{book.description[:179]}…'
                    if len(book.description) > 180
                    else book.description
                ),
                'cover': book.cover,
                'category': book.category.name,
            }
            for book in page.object_list
        ],
        'total_count': paginator.count,
        'has_next': page.has_next(),
        'next_page': page.next_page_number() if page.has_next() else None,
    }
    return JsonResponse(data)

@require_safe
def detail(request, book_pk):
    book = get_object_or_404(Book, pk=book_pk)
    recommendations, recommendation_mode = get_recommendations(book)
    context = {
        "book": book,
        "recommendations": recommendations,
        "recommendation_mode": recommendation_mode,
    }
    return render(request, "books/detail.html", context)

@login_required
@require_http_methods(["GET", "POST"])
def thread_create(request, book_pk):
    book = Book.objects.get(pk=book_pk)
    if request.method == "POST":
        form = ThreadForm(request.POST, request.FILES)
        if form.is_valid():
            thread = form.save(commit=False)
            thread.book = book
            thread.user = request.user
            thread.save()

            generated_image_path = generate_image_with_openai(thread.title, thread.content, book.title, book.author)
            if generated_image_path:
                thread.cover_img = generated_image_path
                thread.save()
                
            return redirect("books:thread_detail", book.pk, thread.pk)
    else:
        form = ThreadForm()
    context = {
        "form": form,
        "book": book,
    }
    return render(request, "books/thread_create.html", context)


@login_required
@require_safe
def thread_detail(request, book_pk, thread_pk):
    book = get_object_or_404(Book, pk=book_pk)
    thread = get_object_or_404(Thread, pk=thread_pk, book=book)
    comment_form = CommentForm()
    context = {
        "book" : book,
        "thread": thread,
        "comment_form" : comment_form,
        "is_liked": thread.likes.filter(pk=request.user.pk).exists(),
        "likes_count": thread.likes.count(),
    }
    return render(request, "books/thread_detail.html", context)



@login_required
@require_http_methods(["GET", "POST"])
def thread_update(request, book_pk, thread_pk):
    book = Book.objects.get(pk=book_pk)
    thread = Thread.objects.get(pk=thread_pk)
    comment_form = CommentForm(request.POST)
    if thread.user == request.user:
        if request.method == "POST":
            form = ThreadForm(request.POST, request.FILES, instance=thread)
            if form.is_valid():
                form.save()  
                return redirect('books:thread_detail', book_pk=book.pk, thread_pk=thread.pk)
        else:
            form = ThreadForm(instance=thread)
    else :
        return redirect('books:index') 
    context = {
        "form": form,
        "book": book,
        "comment_form" : comment_form,
    }
    return render(request, "books/thread_update.html", context)


@login_required
@require_POST
def thread_delete(request, book_pk, thread_pk):
    thread = Thread.objects.get(pk=thread_pk)
    if thread.user == request.user:
        thread.delete()
    return redirect("books:detail", book_pk)


# 쓰레드 좋아요 비동기 처리
@require_POST
def likes(request, book_pk, thread_pk):
    if not request.user.is_authenticated:
        return JsonResponse({'error': '로그인 후 이용해 주세요.'}, status=401)

    book = get_object_or_404(Book, pk=book_pk)
    thread = get_object_or_404(Thread, pk=thread_pk, book=book)
    is_liked = thread.likes.filter(pk=request.user.pk).exists()

    # ManyToMany 관계를 이용해 좋아요를 중복 저장하지 않고 토글한다.
    if is_liked:
        thread.likes.remove(request.user)
    else:
        thread.likes.add(request.user)

    return JsonResponse({
        'is_liked': not is_liked,
        'likes_count': thread.likes.count(),
    })

# 쓰레드 댓글 비동기 처리
@require_POST
def create_comment(request, book_pk, thread_pk):
    if not request.user.is_authenticated:
        return JsonResponse({'error': '로그인 후 이용해 주세요.'}, status=401)

    book = get_object_or_404(Book, pk=book_pk)
    thread = get_object_or_404(Thread, pk=thread_pk, book=book)
    form = CommentForm(request.POST)
    if not form.is_valid():
        return JsonResponse({
            'error': '댓글 내용을 확인해 주세요.',
            'errors': form.errors.get_json_data(),
        }, status=400)

    comment = form.save(commit=False)
    comment.user = request.user
    comment.thread = thread
    comment.save()

    return JsonResponse({
        'id': comment.pk,
        'content': comment.content,
        'username': comment.user.username,
        'profile_url': reverse('accounts:profile', args=[comment.user.username]),
        'created_at': comment.created_at.isoformat(),
        'delete_url': reverse('books:delete_comment', args=[book.pk, comment.pk]),
    }, status=201)

@require_POST
def delete_comment(request, book_pk, comment_pk):
    if not request.user.is_authenticated:
        return JsonResponse({'error': '로그인 후 이용해 주세요.'}, status=401)

    comment = get_object_or_404(
        Comment.objects.select_related('thread'),
        pk=comment_pk,
        thread__book_id=book_pk,
    )
    if comment.user_id != request.user.pk:
        return JsonResponse({'error': '작성한 댓글만 삭제할 수 있습니다.'}, status=403)

    comment.delete()
    return JsonResponse({'deleted': True, 'comment_id': comment_pk})
