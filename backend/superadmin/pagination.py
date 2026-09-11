from rest_framework.pagination import PageNumberPagination


class SuperAdminPagination(PageNumberPagination):
    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 200


def paginate(request, queryset, serializer_cls, context=None):
    """Shared server-side pagination helper (spec sections 56/57) for the
    plain-APIView-based endpoints in this app -- mirrors the existing
    `AdminSubscriptionListView` convention of a flat `{results, total}` shape,
    plus `page`/`num_pages` so the frontend's DataTable can page through."""
    paginator = SuperAdminPagination()
    page = paginator.paginate_queryset(queryset, request)
    serializer = serializer_cls(page, many=True, context=context or {})
    return {
        "results": serializer.data,
        "total": paginator.page.paginator.count,
        "page": paginator.page.number,
        "num_pages": paginator.page.paginator.num_pages,
    }
