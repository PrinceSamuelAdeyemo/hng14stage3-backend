from django.urls import path
from .views import ProfileListView, ProfileSearchView

urlpatterns = [
    path('profiles', ProfileListView.as_view(), name='profile-list'),
    path('profiles/search', ProfileSearchView.as_view(), name='profile-search'),
]
