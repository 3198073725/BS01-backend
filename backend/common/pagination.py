from rest_framework.pagination import PageNumberPagination

class StandardResultsSetPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = 'page_size'
    max_page_size = 50

    def format(self, data, total):
        return {
            'results': data,
            'page': self.page.number,
            'page_size': self.get_page_size(self.request),
            'total': total,
            'has_next': self.page.has_next(),
        }
